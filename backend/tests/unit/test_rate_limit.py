"""Rate limiting: pacing under a per-minute quota, 429 retry with back-off, then fallback.
A fake clock makes every wait instantaneous and exactly measurable."""

from __future__ import annotations

from typing import NoReturn

import httpx2
import openai
import pytest
from pydantic import BaseModel

from ifap.adapters.llm.openai_compatible import OpenAICompatibleLLMClient
from ifap.adapters.llm.rate_limit import (
    RateLimitWaitExceededError,
    SlidingWindowRateLimiter,
    retry_delay_seconds,
)
from ifap.application.ports import LLMUnavailableError
from ifap.config.settings import LLM_PRESETS, LLMProviderKind, LLMSettings


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _limiter(time: FakeTime, limit: int = 2, max_wait: float = 120) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(
        limit, max_wait_seconds=max_wait, clock=time.clock, sleep=time.sleep
    )


# ---------------------------------------------------------------- limiter


async def test_limiter_queues_once_the_window_is_full() -> None:
    time = FakeTime()
    limiter = _limiter(time)
    assert await limiter.acquire() == 0
    time.now = 10
    assert await limiter.acquire() == 0
    assert await limiter.acquire() == 50  # oldest call (t=0) leaves the 60 s window at t=60
    assert time.sleeps == [50]


async def test_old_calls_age_out_of_the_window() -> None:
    time = FakeTime()
    limiter = _limiter(time)
    await limiter.acquire()
    await limiter.acquire()
    time.now = 61
    assert await limiter.acquire() == 0


async def test_block_for_pauses_all_callers() -> None:
    time = FakeTime()
    limiter = _limiter(time, limit=10)
    limiter.block_for(23)
    assert await limiter.acquire() == 23


async def test_wait_beyond_budget_fails_fast() -> None:
    time = FakeTime()
    limiter = _limiter(time, limit=1, max_wait=30)
    await limiter.acquire()
    with pytest.raises(RateLimitWaitExceededError) as raised:
        await limiter.acquire()
    assert raised.value.wait_seconds == 60
    assert not time.sleeps


# ---------------------------------------------------------------- retry hints


def _rate_limit_error(
    *, headers: dict[str, str] | None = None, body: object = None
) -> openai.RateLimitError:
    request = httpx2.Request("POST", "https://llm.example/v1/chat/completions")
    response = httpx2.Response(429, request=request, headers=headers or {})
    return openai.RateLimitError("quota exceeded", response=response, body=body)


GOOGLE_BODY = [{"error": {"code": 429, "details": [{"@type": "Help"}, {"retryDelay": "23s"}]}}]


def test_retry_delay_from_header_body_or_default() -> None:
    assert retry_delay_seconds(_rate_limit_error(headers={"retry-after": "7"}), 10) == 7
    assert retry_delay_seconds(_rate_limit_error(body=GOOGLE_BODY), 10) == 23
    assert retry_delay_seconds(_rate_limit_error(body={"error": {"details": []}}), 10) == 10
    assert retry_delay_seconds(_rate_limit_error(body="not json"), 10) == 10
    assert retry_delay_seconds(_rate_limit_error(body=[]), 10) == 10


# ---------------------------------------------------------------- adapter behaviour


class Answer(BaseModel):
    value: str


class ScriptedRunnable:
    """Raises or returns the scripted outcomes in order."""

    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def ainvoke(self, messages: object) -> object:
        del messages
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ScriptedChat:
    def __init__(self, runnable: ScriptedRunnable) -> None:
        self.runnable = runnable

    def with_structured_output(self, schema: object) -> ScriptedRunnable:
        del schema
        return self.runnable

    def bind_tools(self, tools: list[dict[str, object]]) -> NoReturn:
        raise AssertionError(f"bind_tools not used in this test: {tools}")

    async def ainvoke(self, messages: object) -> NoReturn:
        raise AssertionError(f"plain ainvoke not used in this test: {messages}")


def _client(
    time: FakeTime, runnable: ScriptedRunnable, **overrides: object
) -> OpenAICompatibleLLMClient:
    settings = LLMSettings.model_validate({"provider": LLMProviderKind.OLLAMA, **overrides})
    return OpenAICompatibleLLMClient(
        settings,
        chat_factory=lambda _model: ScriptedChat(runnable),
        sleep=time.sleep,
        clock=time.clock,
    )


async def _ask(client: OpenAICompatibleLLMClient) -> Answer:
    return await client.generate_structured(system="s", user="u", output_type=Answer)


async def test_429_is_retried_after_the_provider_delay() -> None:
    time = FakeTime()
    runnable = ScriptedRunnable(_rate_limit_error(body=GOOGLE_BODY), Answer(value="ok"))
    client = _client(time, runnable)  # no pacing configured
    assert await _ask(client) == Answer(value="ok")
    assert time.sleeps == [23]
    assert runnable.calls == 2


async def test_429_with_pacing_blocks_the_shared_limiter() -> None:
    time = FakeTime()
    runnable = ScriptedRunnable(
        _rate_limit_error(headers={"retry-after": "12"}), Answer(value="ok")
    )
    client = _client(time, runnable, requests_per_minute=8)
    assert await _ask(client) == Answer(value="ok")
    assert time.sleeps == [12]  # waited inside the limiter, so other callers wait too


async def test_falls_back_when_retries_are_exhausted() -> None:
    time = FakeTime()
    runnable = ScriptedRunnable(
        _rate_limit_error(body=GOOGLE_BODY), _rate_limit_error(body=GOOGLE_BODY)
    )
    client = _client(time, runnable)
    with pytest.raises(
        LLMUnavailableError, match=r"qwen3:8b: quota exhausted \(429\), resets in 23s"
    ):
        await _ask(client)
    assert runnable.calls == 2


async def test_long_back_off_falls_back_immediately() -> None:
    time = FakeTime()
    runnable = ScriptedRunnable(_rate_limit_error(headers={"retry-after": "300"}))
    client = _client(time, runnable, rate_limit_max_wait_seconds=30)
    with pytest.raises(LLMUnavailableError, match=r"resets in 5m00s"):
        await _ask(client)
    assert not time.sleeps  # never wait longer than the budget


async def test_queue_full_falls_back() -> None:
    time = FakeTime()
    runnable = ScriptedRunnable(Answer(value="first"))
    client = _client(time, runnable, requests_per_minute=1, rate_limit_max_wait_seconds=5)
    assert await _ask(client) == Answer(value="first")
    with pytest.raises(LLMUnavailableError, match="busy: next request slot in 60s"):
        await _ask(client)  # the next slot is ~60 s away, beyond the 5 s budget


def test_gemini_preset_paces_requests() -> None:
    assert LLM_PRESETS[LLMProviderKind.GEMINI].requests_per_minute == 5
    assert LLMSettings(provider=LLMProviderKind.GEMINI).resolved_requests_per_minute == 5
    assert LLMSettings(provider=LLMProviderKind.OLLAMA).resolved_requests_per_minute is None
    explicit = LLMSettings(provider=LLMProviderKind.GEMINI, requests_per_minute=4)
    assert explicit.resolved_requests_per_minute == 4
