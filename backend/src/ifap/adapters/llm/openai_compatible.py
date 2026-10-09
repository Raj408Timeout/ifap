"""LLM adapters behind the `LLMClient` port.

`OpenAICompatibleLLMClient` uses LangChain so any OpenAI-compatible endpoint (Ollama, Gemini,
OpenAI, Azure OpenAI, vLLM, LiteLLM proxy) works by changing `base_url`. It offers two call
styles: structured output (`generate_structured`) and tool calling (`converse`). LangChain
message types stay inside this module - the core only sees the port's DTOs.

Resilience, per call:

1. **Model chain** - the primary model, then the configured fallbacks. Free-tier quotas are
   counted per model, so each fallback adds its own daily capacity.
2. **Pacing** - each model has a sliding-window limiter under its per-minute quota.
3. **Per-model failure policy**
   * HTTP 429 with a short back-off  -> wait and retry the same model
   * HTTP 429 with a long back-off   -> model unavailable until its quota resets
   * 5xx / timeout / connection error -> model cooled down (`model_cooldown_seconds`)
   * 404 model not found              -> model skipped for the life of the process
   * anything else (401, 400, ...)    -> stop: every model would fail the same way
4. When no model can answer, `LLMUnavailableError` lets the agent fall back to its heuristic.

Every successful call reports the model that answered (`record_model_use`), so traces and
the UI always show which model produced each step.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import openai
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ifap.adapters.llm.rate_limit import (
    Clock,
    RateLimitWaitExceededError,
    Sleep,
    SlidingWindowRateLimiter,
    retry_delay_seconds,
)
from ifap.application.llm_usage import record_model_use
from ifap.application.ports import (
    AssistantTurn,
    ChatMessage,
    LLMUnavailableError,
    ModelState,
    ToolCall,
    ToolSpec,
)
from ifap.config.settings import LLMSettings
from ifap.observability.logging import get_logger

_log = get_logger(__name__)
_MAX_REASON_LENGTH = 160  # one provider error
_MAX_SUMMARY_LENGTH = 300  # "all models unavailable - ..." across the chain

_HOUR = 3600
_MINUTE = 60

ModelCall = Callable[[list[BaseMessage]], Awaitable[object]]


class Invocable(Protocol):
    def ainvoke(self, messages: list[BaseMessage], /) -> Awaitable[object]: ...


class ChatModel(Protocol):
    """The slice of LangChain's chat model interface IFAP uses (keeps `Any` out of the core)."""

    def with_structured_output(self, schema: type[BaseModel]) -> Invocable: ...

    def bind_tools(self, tools: list[dict[str, object]]) -> Invocable: ...

    def ainvoke(self, messages: list[BaseMessage], /) -> Awaitable[object]: ...


ChatFactory = Callable[[str], ChatModel]  # model name -> chat model
Prepare = Callable[[ChatModel], ModelCall]  # chat model -> the call to make on it


class _ModelUnavailableError(Exception):
    """This model cannot answer now; try the next one in the chain."""

    def __init__(self, reason: str, retry_after: float) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


@dataclass(slots=True)
class _ModelSlot:
    name: str
    chat: ChatModel
    limiter: SlidingWindowRateLimiter | None
    unavailable_until: float = 0.0
    reason: str | None = None


def default_chat_factory(settings: LLMSettings) -> ChatFactory:
    def build(model: str) -> ChatModel:
        # LangChain's generics are partially untyped; this cast is the typed boundary.
        chat: Any = ChatOpenAI(
            model=model,
            api_key=settings.resolved_api_key,
            base_url=settings.resolved_base_url,
            temperature=settings.temperature,
            timeout=settings.resolved_timeout_seconds,
            max_retries=settings.max_retries,
            reasoning_effort=settings.resolved_reasoning_effort,
        )
        return cast(ChatModel, chat)

    return build


class OpenAICompatibleLLMClient:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        chat_factory: ChatFactory | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        build = chat_factory or default_chat_factory(settings)
        rpm = settings.resolved_requests_per_minute
        self._max_wait = settings.rate_limit_max_wait_seconds
        self._retries = settings.rate_limit_retries
        self._default_delay = settings.rate_limit_default_retry_seconds
        self._cooldown = settings.model_cooldown_seconds
        self._sleep = sleep
        self._clock = clock
        self._slots = [
            _ModelSlot(
                name=name,
                chat=build(name),
                limiter=(
                    SlidingWindowRateLimiter(
                        rpm, max_wait_seconds=self._max_wait, clock=clock, sleep=sleep
                    )
                    if rpm
                    else None
                ),
            )
            for name in settings.resolved_models
        ]

    @property
    def enabled(self) -> bool:
        return True

    def model_states(self) -> list[ModelState]:
        now = self._clock()
        return [_state(slot, now) for slot in self._slots]

    async def generate_structured[T: BaseModel](
        self, *, system: str, user: str, output_type: type[T]
    ) -> T:
        messages: list[BaseMessage] = [SystemMessage(system), HumanMessage(user)]

        def prepare(chat: ChatModel) -> ModelCall:
            return chat.with_structured_output(output_type).ainvoke

        result = await self._call(prepare, messages, purpose=output_type.__name__)
        if not isinstance(result, output_type):
            raise LLMUnavailableError(f"Unexpected LLM output: {type(result).__name__}")
        return result

    async def converse(
        self, *, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> AssistantTurn:
        history: list[BaseMessage] = [SystemMessage(system), *(_to_langchain(m) for m in messages)]
        openai_tools = [_to_openai_tool(tool) for tool in tools]

        def prepare(chat: ChatModel) -> ModelCall:
            return chat.bind_tools(openai_tools).ainvoke if openai_tools else chat.ainvoke

        response = await self._call(prepare, history, purpose="converse")
        if not isinstance(response, AIMessage):
            raise LLMUnavailableError(f"Unexpected LLM output: {type(response).__name__}")
        return _to_turn(response)

    async def _call(self, prepare: Prepare, messages: list[BaseMessage], *, purpose: str) -> object:
        skipped: list[str] = []
        for slot in self._slots:
            if slot.unavailable_until > self._clock():
                skipped.append(f"{slot.name}: {slot.reason}")
                continue
            try:
                result = await self._call_model(slot, prepare(slot.chat), messages, purpose)
            except _ModelUnavailableError as exc:
                self._mark_unavailable(slot, exc, purpose)
                skipped.append(f"{slot.name}: {exc.reason}")
                continue
            slot.reason = None
            record_model_use(slot.name)
            return result
        summary = "; ".join(skipped)
        raise LLMUnavailableError(f"all models unavailable - {summary}"[:_MAX_SUMMARY_LENGTH])

    async def _call_model(
        self, slot: _ModelSlot, call: ModelCall, messages: list[BaseMessage], purpose: str
    ) -> object:
        attempt = 0
        while True:
            await self._acquire(slot, purpose)
            try:
                return await call(messages)
            except openai.RateLimitError as exc:
                await self._back_off(slot, exc, attempt, purpose)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                raise self._classify(slot, exc, purpose) from exc
            attempt += 1

    def _classify(self, slot: _ModelSlot, exc: Exception, purpose: str) -> Exception:
        """Model-level failures try the next model; anything else stops the chain."""
        if isinstance(exc, openai.NotFoundError):
            return _ModelUnavailableError("model not found (404)", math.inf)
        if isinstance(exc, openai.InternalServerError):
            return _ModelUnavailableError(f"server error {exc.status_code}", self._cooldown)
        if isinstance(exc, openai.APIConnectionError):  # includes timeouts
            return _ModelUnavailableError(type(exc).__name__, self._cooldown)
        _log.warning("llm.failed", model=slot.name, error=str(exc), purpose=purpose)
        return LLMUnavailableError(f"{type(exc).__name__}: {exc}"[:_MAX_REASON_LENGTH])

    async def _acquire(self, slot: _ModelSlot, purpose: str) -> None:
        if slot.limiter is None:
            return
        try:
            waited = await slot.limiter.acquire()
        except RateLimitWaitExceededError as exc:
            reason = f"busy: next request slot in {exc.wait_seconds:.0f}s"
            raise _ModelUnavailableError(reason, exc.wait_seconds) from exc
        if waited:
            _log.info("llm.queued", model=slot.name, purpose=purpose, waited_seconds=round(waited))

    async def _back_off(
        self, slot: _ModelSlot, error: openai.RateLimitError, attempt: int, purpose: str
    ) -> None:
        delay = retry_delay_seconds(error, self._default_delay)
        _log.warning("llm.rate_limited", model=slot.name, purpose=purpose, retry_in=delay)
        if attempt >= self._retries or delay > self._max_wait:
            reason = f"quota exhausted (429), resets in {_human(delay)}"
            raise _ModelUnavailableError(reason, delay) from error
        if slot.limiter is not None:
            slot.limiter.block_for(delay)  # every queued caller of this model waits it out
        else:
            await self._sleep(delay)

    def _mark_unavailable(
        self, slot: _ModelSlot, exc: _ModelUnavailableError, purpose: str
    ) -> None:
        slot.unavailable_until = self._clock() + exc.retry_after
        slot.reason = exc.reason
        _log.warning("llm.model_unavailable", model=slot.name, reason=exc.reason, purpose=purpose)


def _state(slot: _ModelSlot, now: float) -> ModelState:
    remaining = slot.unavailable_until - now
    if remaining <= 0:
        return ModelState(name=slot.name, available=True)
    return ModelState(
        name=slot.name,
        available=False,
        reason=slot.reason,
        available_in_seconds=None if math.isinf(remaining) else math.ceil(remaining),
    )


def _human(seconds: float) -> str:
    if seconds >= _HOUR:
        return f"{int(seconds // _HOUR)}h{int(seconds % _HOUR // _MINUTE):02d}m"
    if seconds >= _MINUTE:
        return f"{int(seconds // _MINUTE)}m{int(seconds % _MINUTE):02d}s"
    return f"{seconds:.0f}s"


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

    def model_states(self) -> list[ModelState]:
        return []

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
