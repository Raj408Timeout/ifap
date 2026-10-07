"""LLM adapters behind the `LLMClient` port.

`OpenAICompatibleLLMClient` uses LangChain's structured output so any OpenAI-compatible
endpoint (Ollama, OpenAI, Azure OpenAI, vLLM, LiteLLM proxy) works by changing `base_url`.
Every provider error is translated to `LLMUnavailableError`, so agents degrade gracefully.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ifap.application.ports import LLMUnavailableError
from ifap.config.settings import LLMSettings
from ifap.observability.logging import get_logger

_log = get_logger(__name__)
_MAX_REASON_LENGTH = 160


class OpenAICompatibleLLMClient:
    def __init__(self, settings: LLMSettings) -> None:
        # LangChain's generics are partially untyped; `Any` marks the typed boundary.
        self._chat: Any = ChatOpenAI(
            model=settings.resolved_model,
            api_key=settings.resolved_api_key,
            base_url=settings.resolved_base_url,
            temperature=settings.temperature,
            timeout=settings.resolved_timeout_seconds,
            max_retries=settings.max_retries,
            reasoning_effort=settings.resolved_reasoning_effort,
        )

    @property
    def enabled(self) -> bool:
        return True

    async def generate_structured[T: BaseModel](
        self, *, system: str, user: str, output_type: type[T]
    ) -> T:
        runnable: Any = self._chat.with_structured_output(output_type)
        try:
            result: object = await runnable.ainvoke([SystemMessage(system), HumanMessage(user)])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log.warning("llm.failed", error=str(exc), output_type=output_type.__name__)
            reason = f"{type(exc).__name__}: {exc}"[:_MAX_REASON_LENGTH]
            raise LLMUnavailableError(reason) from exc
        if not isinstance(result, output_type):
            raise LLMUnavailableError(f"Unexpected LLM output: {type(result).__name__}")
        return result


class DisabledLLMClient:
    """Null Object used when no API key is configured: agents use deterministic strategies."""

    @property
    def enabled(self) -> bool:
        return False

    async def generate_structured[T: BaseModel](
        self, *, system: str, user: str, output_type: type[T]
    ) -> T:
        del system, user
        raise LLMUnavailableError(f"No LLM configured for {output_type.__name__}")
