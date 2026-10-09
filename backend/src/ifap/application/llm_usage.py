"""Which LLM model(s) actually answered during one agent step.

The LLM adapter calls `record_model_use(model)` after every successful call; `BaseAgent.run`
wraps each execution in `track_model_use()` and copies the result into the agent's trace row.
A context variable keeps this out of every port and agent signature, and isolates concurrent
requests (each asyncio task has its own context). The bucket is a mutable list, so calls made
in tasks spawned by libraries during the step are still attributed to it.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[list[str] | None] = ContextVar("ifap_llm_models_used", default=None)


def record_model_use(model: str) -> None:
    bucket = _current.get()
    if bucket is not None:
        bucket.append(model)


@contextmanager
def track_model_use() -> Generator[list[str]]:
    bucket: list[str] = []
    token = _current.set(bucket)
    try:
        yield bucket
    finally:
        _current.reset(token)
