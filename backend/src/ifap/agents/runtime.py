"""Tool-loop runtime for autonomous agents.

`run_tool_loop` is the classic agent loop: the model sees the goal and a set of tools, decides
which tool to call, sees the result, and repeats until it answers without calling a tool.

Guardrails (the part that makes autonomy safe to run unattended):

* `max_tool_calls` - hard cap on tool executions per run;
* `max_seconds`    - wall-clock budget, checked before every model turn;
* tool failures (unknown tool, invalid arguments, domain errors) are returned to the model as
  error results instead of crashing the agent, so it can correct itself.

The loop speaks only the `LLMClient` port (`converse`), so it works with any provider.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from ifap.application.ports import AssistantTurn, ChatMessage, LLMClient, ToolCall, ToolSpec
from ifap.domain.errors import IFAPError

_RESULT_PREVIEW_CHARS = 300

ToolHandler = Callable[[Mapping[str, JsonValue]], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class AgentTool:
    spec: ToolSpec
    handler: ToolHandler

    @classmethod
    def from_model(
        cls, *, name: str, description: str, args: type[BaseModel], handler: ToolHandler
    ) -> AgentTool:
        """Declare a tool whose JSON Schema is derived from a Pydantic arguments model."""
        schema: dict[str, JsonValue] = args.model_json_schema()
        return cls(ToolSpec(name=name, description=description, input_schema=schema), handler)


class StopReason(StrEnum):
    COMPLETED = "completed"
    MAX_TOOL_CALLS = "max_tool_calls"
    TIMEOUT = "timeout"


class LoopBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_tool_calls: int
    max_seconds: float


class ToolLoopStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    arguments: dict[str, JsonValue]
    result_preview: str
    is_error: bool


class ToolLoopResult(BaseModel):
    """Stored as the agent's artifact, so every autonomous decision is auditable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    final_message: str
    steps: tuple[ToolLoopStep, ...]
    stop_reason: StopReason
    model_turns: int


async def run_tool_loop(
    llm: LLMClient,
    *,
    system: str,
    goal: str,
    tools: Sequence[AgentTool],
    budget: LoopBudget,
    clock: Callable[[], float] = time.monotonic,
) -> ToolLoopResult:
    """Raises `LLMUnavailableError` if the model cannot be reached on any turn."""
    return await _ToolLoop(llm, system=system, tools=tools, budget=budget, clock=clock).run(goal)


class _ToolLoop:
    def __init__(
        self,
        llm: LLMClient,
        *,
        system: str,
        tools: Sequence[AgentTool],
        budget: LoopBudget,
        clock: Callable[[], float],
    ) -> None:
        self._llm = llm
        self._system = system
        self._tools = {tool.spec.name: tool for tool in tools}
        self._specs = [tool.spec for tool in tools]
        self._budget = budget
        self._clock = clock
        self._messages: list[ChatMessage] = []
        self._steps: list[ToolLoopStep] = []
        self._turns = 0
        self._final = ""

    async def run(self, goal: str) -> ToolLoopResult:
        self._messages.append(ChatMessage(role="user", content=goal))
        started = self._clock()
        while True:
            if self._clock() - started > self._budget.max_seconds:
                return self._result(StopReason.TIMEOUT)
            turn = await self._llm.converse(
                system=self._system, messages=self._messages, tools=self._specs
            )
            self._turns += 1
            self._final = turn.content
            self._messages.append(_assistant(turn))
            if not turn.tool_calls:
                return self._result(StopReason.COMPLETED)
            if not await self._run_calls(turn.tool_calls):
                return self._result(StopReason.MAX_TOOL_CALLS)

    async def _run_calls(self, calls: Sequence[ToolCall]) -> bool:
        """Execute requested calls; False when the tool-call budget is exhausted."""
        for call in calls:
            if len(self._steps) >= self._budget.max_tool_calls:
                return False
            output, is_error = await _execute(self._tools, call)
            self._steps.append(_step(call, output, is_error))
            self._messages.append(ChatMessage(role="tool", content=output, tool_call_id=call.id))
        return True

    def _result(self, reason: StopReason) -> ToolLoopResult:
        return ToolLoopResult(
            final_message=self._final,
            steps=tuple(self._steps),
            stop_reason=reason,
            model_turns=self._turns,
        )


async def _execute(tools: Mapping[str, AgentTool], call: ToolCall) -> tuple[str, bool]:
    tool = tools.get(call.name)
    if tool is None:
        return _error(f"Unknown tool '{call.name}'. Available: {', '.join(sorted(tools))}"), True
    try:
        return await tool.handler(call.arguments), False
    except ValidationError as exc:
        return _error(f"Invalid arguments: {exc.errors(include_url=False)}"), True
    except IFAPError as exc:
        return _error(str(exc)), True


def _error(message: str) -> str:
    return json.dumps({"error": message})


def _assistant(turn: AssistantTurn) -> ChatMessage:
    return ChatMessage(role="assistant", content=turn.content, tool_calls=turn.tool_calls)


def _step(call: ToolCall, output: str, is_error: bool) -> ToolLoopStep:
    return ToolLoopStep(
        tool=call.name,
        arguments=call.arguments,
        result_preview=output[:_RESULT_PREVIEW_CHARS],
        is_error=is_error,
    )
