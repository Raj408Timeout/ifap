"""Client-side rate limiting for LLM providers with per-minute quotas (e.g. Gemini free tier).

* `SlidingWindowRateLimiter` - at most N calls per rolling window, shared by every agent and
  request in the process. Callers queue (FIFO) instead of exceeding the quota.
* `block_for` - after a 429 the provider's requested back-off pauses *all* callers, because
  they share the same quota.
* `retry_delay_seconds` - reads the provider's back-off hint: the `Retry-After` header, or
  Google's `RetryInfo.retryDelay` in the error body ("23s").

Waits are bounded by `max_wait_seconds`; beyond that the call fails fast so the agent can
fall back to its heuristic instead of hanging.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable

import openai
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*s?\s*$")

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class RateLimitWaitExceededError(RuntimeError):
    def __init__(self, wait_seconds: float) -> None:
        super().__init__(f"next request slot in {wait_seconds:.0f}s exceeds the wait budget")
        self.wait_seconds = wait_seconds


class SlidingWindowRateLimiter:
    def __init__(
        self,
        limit: int,
        *,
        max_wait_seconds: float,
        window_seconds: float = 60.0,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._max_wait = max_wait_seconds
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Reserve a slot, queueing if needed. Returns the seconds spent waiting."""
        async with self._lock:  # FIFO: one caller waits at a time, the rest queue behind it
            waited = 0.0
            while (delay := self._delay()) > 0:
                if waited + delay > self._max_wait:
                    raise RateLimitWaitExceededError(delay)
                await self._sleep(delay)
                waited += delay
            self._calls.append(self._clock())
            return waited

    def block_for(self, seconds: float) -> None:
        """Pause every caller (e.g. after a 429 with a retry delay)."""
        self._blocked_until = max(self._blocked_until, self._clock() + seconds)

    def _delay(self) -> float:
        now = self._clock()
        while self._calls and self._calls[0] <= now - self._window:
            self._calls.popleft()
        ready_at = self._blocked_until
        if len(self._calls) >= self._limit:
            ready_at = max(ready_at, self._calls[0] + self._window)
        return max(0.0, ready_at - now)


def retry_delay_seconds(error: openai.RateLimitError, default: float) -> float:
    header = _parse_duration(error.response.headers.get("retry-after"))
    if header is not None:
        return header
    body_delay = _retry_delay_in_body(error.body)
    return body_delay if body_delay is not None else default


class _RetryDetail(BaseModel):
    retry_delay: str | None = Field(default=None, alias="retryDelay")


class _ErrorBody(BaseModel):
    details: list[_RetryDetail] = []


class _Envelope(BaseModel):
    error: _ErrorBody


# Google returns `[{"error": {"details": [{"retryDelay": "23s"}]}}]`; others send a dict,
# and some SDKs hand over the inner `error` object directly.
type _ErrorShape = list[_Envelope] | _Envelope | _ErrorBody
_ERROR_BODY: TypeAdapter[_ErrorShape] = TypeAdapter(_ErrorShape)


def _retry_delay_in_body(body: object) -> float | None:
    try:
        parsed = _ERROR_BODY.validate_python(body)
    except ValidationError:
        return None
    if isinstance(parsed, list):
        if not parsed:
            return None
        parsed = parsed[0]
    error = parsed.error if isinstance(parsed, _Envelope) else parsed
    delays = (_parse_duration(detail.retry_delay) for detail in error.details)
    return next((delay for delay in delays if delay is not None), None)


def _parse_duration(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    match = _DURATION.match(value)
    return float(match.group(1)) if match else None
