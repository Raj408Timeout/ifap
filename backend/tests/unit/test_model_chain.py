"""Model fallback chain: per-model failure policy, recovery, status, and model attribution."""

from __future__ import annotations

from dataclasses import replace
from typing import NoReturn

import httpx2
import openai
import pytest
from pydantic import BaseModel

from ifap.adapters.llm.openai_compatible import OpenAICompatibleLLMClient
from ifap.agents.builtin.intent_agent import IntentAgent, IntentExtraction
from ifap.agents.framework import AgentDependencies
from ifap.application.llm_switch import SwitchableLLMClient
from ifap.application.ports import LLMUnavailableError
from ifap.application.workflow import GenerationRequest, WorkflowState
from ifap.config.settings import LLMProviderKind, LLMSettings

REQUEST = httpx2.Request("POST", "https://llm.example/v1/chat/completions")


def _status_error[E: openai.APIStatusError](kind: type[E], status: int, **headers: str) -> E:
    response = httpx2.Response(status, request=REQUEST, headers=headers)
    return kind(f"HTTP {status}", response=response, body=None)


DAILY_QUOTA = [{"error": {"details": [{"retryDelay": "81917s"}]}}]


def _daily_quota() -> openai.RateLimitError:
    response = httpx2.Response(429, request=REQUEST)
    return openai.RateLimitError("quota", response=response, body=DAILY_QUOTA)


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class Model:
    """One fake model: replays scripted outcomes, then keeps answering `default`."""

    def __init__(self, *outcomes: object, default: object = None) -> None:
        self._outcomes = list(outcomes)
        self._default = default
        self.calls = 0

    def with_structured_output(self, schema: object) -> Model:
        del schema
        return self

    async def ainvoke(self, messages: object) -> object:
        del messages
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else self._default
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def bind_tools(self, tools: list[dict[str, object]]) -> NoReturn:
        raise AssertionError(f"bind_tools not used in this test: {tools}")


class Answer(BaseModel):
    value: str


def _chain(
    time: FakeTime, models: dict[str, Model], **overrides: object
) -> OpenAICompatibleLLMClient:
    names = list(models)
    settings = LLMSettings.model_validate(
        {
            "provider": LLMProviderKind.OLLAMA,
            "model": names[0],
            "fallback_models": names[1:],
            **overrides,
        }
    )
    return OpenAICompatibleLLMClient(
        settings, chat_factory=models.__getitem__, sleep=time.sleep, clock=time.clock
    )


async def _ask(client: OpenAICompatibleLLMClient) -> Answer:
    return await client.generate_structured(system="s", user="u", output_type=Answer)


OK = Answer(value="ok")


async def test_exhausted_daily_quota_moves_to_the_next_model_until_it_resets() -> None:
    time = FakeTime()
    primary = Model(_daily_quota(), default=Answer(value="primary"))
    backup = Model(default=Answer(value="backup"))
    client = _chain(time, {"primary": primary, "backup": backup})

    assert await _ask(client) == Answer(value="backup")
    states = {s.name: s for s in client.model_states()}
    assert not states["primary"].available
    assert states["primary"].available_in_seconds == 81917
    assert "resets in 22h45m" in (states["primary"].reason or "")
    assert states["backup"].available

    assert await _ask(client) == Answer(value="backup")  # primary skipped, not retried
    assert primary.calls == 1

    time.now += 81918  # quota reset
    assert await _ask(client) == Answer(value="primary")
    assert all(s.available for s in client.model_states())


@pytest.mark.parametrize(
    ("error", "cooldown"),
    [
        (_status_error(openai.InternalServerError, 503), 60),
        (openai.APITimeoutError(request=REQUEST), 60),
        (_status_error(openai.NotFoundError, 404), None),  # never comes back in this process
    ],
)
async def test_unhealthy_models_are_skipped(error: Exception, cooldown: int | None) -> None:
    time = FakeTime()
    client = _chain(time, {"flaky": Model(error, default=OK), "steady": Model(default=OK)})
    assert await _ask(client) == OK
    flaky = client.model_states()[0]
    assert not flaky.available
    assert flaky.available_in_seconds == cooldown


async def test_busy_primary_hands_over_instead_of_falling_back() -> None:
    time = FakeTime()
    primary, backup = Model(default=OK), Model(default=Answer(value="backup"))
    client = _chain(
        time,
        {"primary": primary, "backup": backup},
        requests_per_minute=1,
        rate_limit_max_wait_seconds=5,
    )
    assert await _ask(client) == OK
    assert await _ask(client) == Answer(value="backup")  # primary's next slot is 60 s away
    assert "busy" in (client.model_states()[0].reason or "")


async def test_auth_errors_stop_the_chain() -> None:
    time = FakeTime()
    backup = Model(default=OK)
    client = _chain(
        time, {"primary": Model(_status_error(openai.AuthenticationError, 401)), "backup": backup}
    )
    with pytest.raises(LLMUnavailableError, match="AuthenticationError"):
        await _ask(client)
    assert backup.calls == 0  # the same key would fail on every model


async def test_all_models_unavailable_reports_each_reason() -> None:
    time = FakeTime()
    client = _chain(
        time,
        {"a": Model(_daily_quota()), "b": Model(_status_error(openai.InternalServerError, 500))},
    )
    with pytest.raises(LLMUnavailableError) as raised:
        await _ask(client)
    message = str(raised.value)
    assert message.startswith("all models unavailable - a: quota exhausted (429)")
    assert "b: server error 500" in message
    with pytest.raises(LLMUnavailableError, match="all models unavailable"):
        await _ask(client)  # both skipped while unavailable


def test_model_chain_settings() -> None:
    gemini = LLMSettings(provider=LLMProviderKind.GEMINI)
    assert gemini.resolved_models == [
        "gemini-3.6-flash",
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite",
    ]
    single = LLMSettings(provider=LLMProviderKind.GEMINI, fallback_models=[])
    assert single.resolved_models == ["gemini-3.6-flash"]
    deduped = LLMSettings(provider=LLMProviderKind.OLLAMA, model="m", fallback_models=["m", "n"])
    assert deduped.resolved_models == ["m", "n"]


async def test_trace_and_status_show_which_model_answered(deps: AgentDependencies) -> None:
    time = FakeTime()
    extraction = IntentExtraction(survey_type="employee_engagement")
    inner = _chain(time, {"gemini-a": Model(_daily_quota()), "gemini-b": Model(default=extraction)})
    llm = SwitchableLLMClient(inner, provider="gemini", model="gemini-a", enabled=True)

    state = WorkflowState(request=GenerationRequest(message="employee burnout pulse"))
    result = await IntentAgent.create(replace(deps, llm=llm)).run(state)

    assert result.trace[-1].strategy == "llm"
    assert result.trace[-1].models == ("gemini-b",)
    status = llm.status()
    assert status.model == "gemini-b"  # the model the next call will use
    assert [m.name for m in status.models] == ["gemini-a", "gemini-b"]
    assert llm.model_states() == status.models


async def test_heuristic_steps_report_no_model(deps: AgentDependencies) -> None:
    state = WorkflowState(request=GenerationRequest(message="employee burnout pulse"))
    result = await IntentAgent.create(deps).run(state)  # deps use the disabled LLM
    assert result.trace[-1].strategy == "heuristic"
    assert result.trace[-1].models == ()
