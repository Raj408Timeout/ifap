"""Autonomous agents: tool-loop guardrails, the self-correcting builder, signal routing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from itertools import count
from typing import Self

import pytest
from pydantic import BaseModel, JsonValue, ValidationError

from ifap.agents.builtin.autonomous_builder_agent import AGENT_NAME, AutonomousBuilderAgent
from ifap.agents.builtin.intent_agent import IntentAgent
from ifap.agents.builtin.retrieval_agent import TemplateRetrievalAgent
from ifap.agents.builtin.validation_agent import ValidationAgent
from ifap.agents.framework import (
    AgentDependencies,
    AgentDescriptor,
    AgentOutcome,
    AgentRegistry,
    BaseAgent,
    agent_plugin,
)
from ifap.agents.runtime import (
    AgentTool,
    LoopBudget,
    StopReason,
    ToolLoopResult,
    run_tool_loop,
)
from ifap.application.llm_switch import SwitchableLLMClient
from ifap.application.ports import AssistantTurn, ToolCall
from ifap.application.workflow import GenerationRequest, WorkflowState
from ifap.config.settings import WorkflowDefinition, WorkflowSettings
from ifap.domain.errors import DomainRuleViolationError
from ifap.domain.intent import BusinessIntent
from ifap.domain.questionnaire import AnswerType, Question, Questionnaire
from ifap.orchestration.langgraph_orchestrator import LangGraphWorkflowOrchestrator
from tests.fakes import ScriptedLLM


def _call(name: str, call_id: str = "c1", **arguments: JsonValue) -> AssistantTurn:
    return AssistantTurn(tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),))


def _llm(*turns: AssistantTurn) -> SwitchableLLMClient:
    return SwitchableLLMClient(
        ScriptedLLM(turns=turns), provider="test", model="test", enabled=True
    )


# ---------------------------------------------------------------- runtime: tool loop


class EchoArgs(BaseModel):
    text: str


async def _echo(raw: Mapping[str, JsonValue]) -> str:
    return EchoArgs.model_validate(raw).text.upper()


async def _domain_failure(raw: Mapping[str, JsonValue]) -> str:
    raise DomainRuleViolationError(f"rejected {dict(raw)}")


ECHO = AgentTool.from_model(name="echo", description="Echo", args=EchoArgs, handler=_echo)
FAIL = AgentTool(spec=ECHO.spec.model_copy(update={"name": "fail"}), handler=_domain_failure)
BUDGET = LoopBudget(max_tool_calls=5, max_seconds=60)


async def test_tool_loop_runs_tools_until_model_answers() -> None:
    llm = _llm(_call("echo", text="hi"), AssistantTurn(content="done"))
    result = await run_tool_loop(llm, system="s", goal="g", tools=[ECHO], budget=BUDGET)
    assert result.stop_reason is StopReason.COMPLETED
    assert result.final_message == "done"
    assert result.model_turns == 2
    assert [(s.tool, s.result_preview, s.is_error) for s in result.steps] == [("echo", "HI", False)]


async def test_tool_errors_are_returned_to_the_model_not_raised() -> None:
    llm = _llm(
        _call("missing"),
        _call("echo", "c2", wrong="arg"),
        _call("fail", "c3", x=1),
        AssistantTurn(content="gave up"),
    )
    result = await run_tool_loop(llm, system="s", goal="g", tools=[ECHO, FAIL], budget=BUDGET)
    assert [step.is_error for step in result.steps] == [True, True, True]
    assert "Unknown tool 'missing'" in result.steps[0].result_preview
    assert "Invalid arguments" in result.steps[1].result_preview
    assert "rejected" in result.steps[2].result_preview
    assert result.stop_reason is StopReason.COMPLETED


async def test_tool_loop_enforces_max_tool_calls() -> None:
    llm = _llm(*[_call("echo", f"c{i}", text="x") for i in range(10)])
    budget = LoopBudget(max_tool_calls=3, max_seconds=60)
    result = await run_tool_loop(llm, system="s", goal="g", tools=[ECHO], budget=budget)
    assert result.stop_reason is StopReason.MAX_TOOL_CALLS
    assert len(result.steps) == 3


async def test_tool_loop_enforces_time_budget() -> None:
    ticks = count(start=0, step=50)  # every clock read advances 50 s
    llm = _llm(*[_call("echo", f"c{i}", text="x") for i in range(10)])
    budget = LoopBudget(max_tool_calls=10, max_seconds=120)
    result = await run_tool_loop(
        llm, system="s", goal="g", tools=[ECHO], budget=budget, clock=lambda: next(ticks)
    )
    assert result.stop_reason is StopReason.TIMEOUT
    assert result.model_turns == 2


# ---------------------------------------------------------------- autonomous builder


async def _retrieved(deps: AgentDependencies, count_: int = 4) -> WorkflowState:
    request = GenerationRequest(message="patient intake on allergies", question_count=count_)
    state = await IntentAgent.create(deps).run(WorkflowState(request=request))
    return await TemplateRetrievalAgent.create(deps).run(state)


def _builder(deps: AgentDependencies, *turns: AssistantTurn) -> AutonomousBuilderAgent:
    return AutonomousBuilderAgent.create(replace(deps, llm=_llm(*turns)))


async def test_autonomous_builder_self_corrects_until_accepted(deps: AgentDependencies) -> None:
    state = await _retrieved(deps)
    script = (
        _call("search_templates", query="medication allergies", top_k=5),
        _call("submit_draft", "c2", title="Intake", question_ids=["hc-008", "hc-001"]),
        _call("submit_draft", "c3", title="Intake", question_ids=["nope"]),
        _call(
            "submit_draft",
            "c4",
            title="Allergy Intake",
            question_ids=["hc-007", "hc-008", "hc-009", "hc-001"],
        ),
        AssistantTurn(content="Accepted a 4-question intake."),
    )
    result = await _builder(deps, *script).run(state)

    assert result.trace[-1].strategy == "autonomous"
    assert result.questionnaire is not None
    assert result.questionnaire.title == "Allergy Intake"
    assert result.questionnaire.question_ids() == ["hc-001", "hc-007", "hc-008", "hc-009"]
    loop = result.artifact(AGENT_NAME, ToolLoopResult)
    assert loop is not None
    assert [s.tool for s in loop.steps] == [
        "search_templates",
        "submit_draft",
        "submit_draft",
        "submit_draft",
    ]
    first_draft = json.loads(loop.steps[1].result_preview)
    assert first_draft["accepted"] is False  # wrong count -> model had to retry
    assert loop.steps[2].is_error  # unknown id rejected, grounding enforced


async def test_autonomous_builder_falls_back_without_llm(deps: AgentDependencies) -> None:
    state = await _retrieved(deps)
    result = await _builder(deps).run(state)  # empty script -> LLM unavailable
    assert result.trace[-1].strategy == "heuristic"
    assert "fallback: no scripted turn" in result.trace[-1].note
    assert result.questionnaire is not None


async def test_autonomous_builder_falls_back_when_no_draft_accepted(
    deps: AgentDependencies,
) -> None:
    state = await _retrieved(deps)
    result = await _builder(deps, AssistantTurn(content="I cannot do this")).run(state)
    assert result.trace[-1].strategy == "heuristic"
    assert "no accepted draft (stop=completed)" in result.trace[-1].note
    assert result.artifact(AGENT_NAME, ToolLoopResult) is not None


async def test_autonomous_builder_requires_candidates(deps: AgentDependencies) -> None:
    state = WorkflowState(request=GenerationRequest(message="anything at all"))
    result = await _builder(deps).run(state)
    assert result.halted


async def test_retry_visit_includes_validation_feedback(deps: AgentDependencies) -> None:
    state = await _retrieved(deps)
    state = await ValidationAgent.create(deps).run(
        state.model_copy(update={"questionnaire": _tiny_questionnaire()})
    )
    assert state.signal == "incomplete"
    llm = ScriptedLLM()
    agent = AutonomousBuilderAgent.create(
        replace(deps, llm=SwitchableLLMClient(llm, provider="t", model="t", enabled=True))
    )
    await agent.run(state)
    goal = llm.conversations[0][0].content
    assert "Previous attempt had 1 questions" in goal


def _tiny_questionnaire() -> Questionnaire:
    question = Question(id="q1", label="Yes or no?", category="c", answer_type=AnswerType.BOOLEAN)
    return Questionnaire(title="Tiny", survey_type="x", questions=(question,))


# ---------------------------------------------------------------- signal routing & loops


class _Counter(BaseAgent):
    """Emits `again` until it has run `limit` times."""

    limit = 2

    @classmethod
    def create(cls, deps: AgentDependencies) -> Self:
        del deps
        return cls(max_attempts=1, backoff_seconds=0.0)

    async def _execute(self, state: WorkflowState) -> AgentOutcome:
        runs = state.visits(self.descriptor.name) + 1
        return AgentOutcome(state=state.with_signal("again" if runs < self.limit else None))


class _Recorder(BaseAgent):
    @classmethod
    def create(cls, deps: AgentDependencies) -> Self:
        del deps
        return cls(max_attempts=1, backoff_seconds=0.0)

    async def _execute(self, state: WorkflowState) -> AgentOutcome:
        return AgentOutcome(state=state.with_signal("again"))  # always asks to loop


def _graph(
    deps: AgentDependencies, routes: dict[str, dict[str, str]], max_visits: int
) -> LangGraphWorkflowOrchestrator:
    registry = AgentRegistry()
    agent_plugin(AgentDescriptor(name="a", version="1", description="d"), registry)(_Counter)
    agent_plugin(AgentDescriptor(name="b", version="1", description="d"), registry)(_Recorder)
    agents = [registry.create("a", deps), registry.create("b", deps)]
    return LangGraphWorkflowOrchestrator(agents, name="t", routes=routes, max_visits=max_visits)


async def test_signal_routes_create_a_loop(deps: AgentDependencies) -> None:
    orchestrator = _graph(deps, {"b": {"again": "a"}}, max_visits=3)
    state = await orchestrator.run(WorkflowState(request=GenerationRequest(message="loop")))
    # b always asks to go back to a; the visit cap (3) ends the loop
    assert [t.agent for t in state.trace] == ["a", "b", "a", "b", "a", "b"]
    assert orchestrator.name == "t"


async def test_unrouted_signals_are_ignored(deps: AgentDependencies) -> None:
    orchestrator = _graph(deps, {}, max_visits=3)
    state = await orchestrator.run(WorkflowState(request=GenerationRequest(message="line")))
    assert [t.agent for t in state.trace] == ["a", "b"]


def test_orchestrator_rejects_duplicate_steps(deps: AgentDependencies) -> None:
    registry = AgentRegistry()
    agent_plugin(AgentDescriptor(name="a", version="1", description="d"), registry)(_Counter)
    with pytest.raises(ValueError, match="twice"):
        LangGraphWorkflowOrchestrator([registry.create("a", deps), registry.create("a", deps)])


def test_workflow_routes_must_reference_steps() -> None:
    with pytest.raises(ValidationError, match="unknown steps"):
        WorkflowDefinition(steps=["a"], routes={"a": {"x": "ghost"}})


def test_default_workflow_must_exist() -> None:
    with pytest.raises(ValidationError, match="not defined"):
        WorkflowSettings(default_workflow="ghost")


async def test_validation_emits_no_signal_when_complete(deps: AgentDependencies) -> None:
    intent = BusinessIntent(raw_request="r", survey_type="x", question_count=1)
    state = WorkflowState(
        request=GenerationRequest(message="ok then"),
        intent=intent,
        questionnaire=_tiny_questionnaire(),
    )
    result = await ValidationAgent.create(deps).run(state)
    assert result.signal is None


async def test_validation_signals_invalid_questionnaire(deps: AgentDependencies) -> None:
    empty = Questionnaire(title="Empty", survey_type="x")
    state = WorkflowState(request=GenerationRequest(message="ok then"), questionnaire=empty)
    result = await ValidationAgent.create(deps).run(state)
    assert result.signal == "invalid"
    assert "signal=invalid" in result.trace[-1].note


def test_artifact_accessor_checks_type() -> None:
    state = WorkflowState(request=GenerationRequest(message="abc")).with_artifact(
        "x", EchoArgs(text="t")
    )
    assert state.artifact("x", EchoArgs) == EchoArgs(text="t")
    assert state.artifact("x", LoopBudget) is None
    assert state.artifact("missing", EchoArgs) is None
