from __future__ import annotations

from ifap.agents.framework import DEFAULT_REGISTRY, AgentDependencies, load_plugins
from ifap.application.workflow import GenerationRequest, WorkflowState
from ifap.orchestration.langgraph_orchestrator import LangGraphWorkflowOrchestrator


def _orchestrator(deps: AgentDependencies, pipeline: list[str]) -> LangGraphWorkflowOrchestrator:
    load_plugins(["ifap.agents.builtin"])
    return LangGraphWorkflowOrchestrator([DEFAULT_REGISTRY.create(n, deps) for n in pipeline])


async def test_full_pipeline_produces_valid_questionnaire(deps: AgentDependencies) -> None:
    steps = deps.workflow.workflows["standard"].steps
    orchestrator = _orchestrator(deps, steps)
    request = GenerationRequest(message="Patient intake form covering allergies and medication")
    state = await orchestrator.run(WorkflowState(request=request))
    assert [t.agent for t in state.trace] == steps
    assert state.questionnaire is not None
    assert state.validation is not None
    assert state.validation.is_valid
    assert state.questionnaire.survey_type == "healthcare_assessment"


async def test_pipeline_short_circuits_when_an_agent_halts(deps: AgentDependencies) -> None:
    # Builder without retrieval has no candidates -> halts; validation must not run.
    orchestrator = _orchestrator(deps, ["intent", "questionnaire_builder", "validation"])
    state = await orchestrator.run(
        WorkflowState(request=GenerationRequest(message="customer survey"))
    )
    assert state.halted
    assert [t.agent for t in state.trace] == ["intent", "questionnaire_builder"]
