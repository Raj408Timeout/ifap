"""Shared test doubles (real port implementations, no mocking library)."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from ifap.application.ports import AssistantTurn, ChatMessage, LLMUnavailableError, ToolSpec


class ScriptedLLM:
    """Fake LLM: canned structured responses per output type, and a script of tool-calling
    turns for `converse` (an exhausted script means the model is unavailable)."""

    def __init__(self, *responses: BaseModel, turns: Sequence[AssistantTurn] = ()) -> None:
        self._responses = {type(response): response for response in responses}
        self._turns = list(turns)
        self.calls = 0
        self.conversations: list[list[ChatMessage]] = []

    @property
    def enabled(self) -> bool:
        return True

    async def generate_structured[T: BaseModel](
        self, *, system: str, user: str, output_type: type[T]
    ) -> T:
        del system, user
        self.calls += 1
        response = self._responses.get(output_type)
        if not isinstance(response, output_type):
            raise LLMUnavailableError(f"no scripted {output_type.__name__}")
        return response

    async def converse(
        self, *, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> AssistantTurn:
        del system, tools
        self.conversations.append(list(messages))
        if not self._turns:
            raise LLMUnavailableError("no scripted turn")
        return self._turns.pop(0)
