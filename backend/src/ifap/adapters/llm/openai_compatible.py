"""LLM adapters behind the `LLMClient` port.

`OpenAICompatibleLLMClient` uses LangChain so any OpenAI-compatible endpoint (Ollama, OpenAI,
Azure OpenAI, vLLM, LiteLLM proxy) works by changing `base_url`. It offers two call styles:
structured output (`generate_structured`) and tool calling (`converse`). LangChain message
types stay inside this module - the core only sees the port's `ChatMessage`/`ToolCall` DTOs.
Every provider error is translated to `LLMUnavailableError`, so agents degrade gracefully.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ifap.application.ports import (
    AssistantTurn,
    ChatMessage,
    LLMUnavailableError,
    ToolCall,
    ToolSpec,
)
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
        messages: list[BaseMessage] = [SystemMessage(system), HumanMessage(user)]
        result = await _invoke(runnable.ainvoke, messages, purpose=output_type.__name__)
        if not isinstance(result, output_type):
            raise LLMUnavailableError(f"Unexpected LLM output: {type(result).__name__}")
        return result

    async def converse(
        self, *, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> AssistantTurn:
        model: Any = (
            self._chat.bind_tools([_to_openai_tool(t) for t in tools]) if tools else self._chat
        )
        history: list[BaseMessage] = [SystemMessage(system), *(_to_langchain(m) for m in messages)]
        response = await _invoke(model.ainvoke, history, purpose="converse")
        if not isinstance(response, AIMessage):
            raise LLMUnavailableError(f"Unexpected LLM output: {type(response).__name__}")
        return _to_turn(response)


async def _invoke(
    call: Callable[[list[BaseMessage]], Awaitable[object]],
    messages: list[BaseMessage],
    *,
    purpose: str,
) -> object:
    try:
        return await call(messages)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _log.warning("llm.failed", error=str(exc), purpose=purpose)
        reason = f"{type(exc).__name__}: {exc}"[:_MAX_REASON_LENGTH]
        raise LLMUnavailableError(reason) from exc


def _to_openai_tool(spec: ToolSpec) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema,
        },
    }


def _to_langchain(message: ChatMessage) -> BaseMessage:
    if message.role == "user":
        return HumanMessage(message.content)
    if message.role == "tool":
        return ToolMessage(message.content, tool_call_id=message.tool_call_id or "")
    calls = [
        {"id": call.id, "name": call.name, "args": call.arguments, "type": "tool_call"}
        for call in message.tool_calls
    ]
    return AIMessage(content=message.content, tool_calls=calls)


def _to_turn(response: AIMessage) -> AssistantTurn:
    calls = tuple(
        ToolCall(id=call.get("id") or f"call_{index}", name=call["name"], arguments=call["args"])
        for index, call in enumerate(response.tool_calls)
    )
    content = response.content if isinstance(response.content, str) else str(response.content)
    return AssistantTurn(content=content, tool_calls=calls)


class DisabledLLMClient:
    """Null Object used when no LLM is configured: agents use deterministic strategies."""

    @property
    def enabled(self) -> bool:
        return False

    async def generate_structured[T: BaseModel](
        self, *, system: str, user: str, output_type: type[T]
    ) -> T:
        del system, user
        raise LLMUnavailableError(f"No LLM configured for {output_type.__name__}")

    async def converse(
        self, *, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> AssistantTurn:
        del system, messages, tools
        raise LLMUnavailableError("No LLM configured for tool calling")
