"""LLM on/off switch, provider settings, and heuristic fallback in the LLM-backed agents."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ifap.agents.builtin.builder_agent import BuilderPlan, LabelRewrite, QuestionnaireBuilderAgent
from ifap.agents.builtin.intent_agent import IntentAgent, IntentExtraction
from ifap.agents.builtin.retrieval_agent import TemplateRetrievalAgent
from ifap.agents.framework import AgentDependencies
from ifap.application.llm_switch import SwitchableLLMClient
from ifap.application.ports import LLMUnavailableError
from ifap.application.workflow import GenerationRequest, WorkflowState
from ifap.config.settings import (
    OLLAMA_DEFAULT_BASE_URL,
    OLLAMA_DEFAULT_MODEL,
    LLMProviderKind,
    LLMSettings,
    unrecognised_env_vars,
)
from ifap.domain.errors import DomainRuleViolationError
from tests.fakes import ScriptedLLM


def _switch(inner: ScriptedLLM | None, *, enabled: bool = True) -> SwitchableLLMClient:
    return SwitchableLLMClient(inner, provider="ollama", model="test-model", enabled=enabled)


def _state(message: str = "employee burnout pulse survey", count: int = 6) -> WorkflowState:
    return WorkflowState(request=GenerationRequest(message=message, question_count=count))


# ---------------------------------------------------------------- settings


def test_provider_defaults_to_disabled() -> None:
    assert LLMSettings().kind is LLMProviderKind.DISABLED
    assert not LLMSettings().configured


def test_api_key_alone_implies_openai_compatible() -> None:
    assert LLMSettings(api_key="sk-test").kind is LLMProviderKind.OPENAI_COMPATIBLE  # type: ignore[arg-type]


def test_ollama_preset_needs_no_key_or_url() -> None:
    settings = LLMSettings(provider=LLMProviderKind.OLLAMA)
    assert settings.resolved_base_url == OLLAMA_DEFAULT_BASE_URL
    assert settings.resolved_model == OLLAMA_DEFAULT_MODEL
    assert settings.resolved_api_key is not None
    assert settings.resolved_reasoning_effort == "none"
    assert settings.resolved_timeout_seconds > LLMSettings().resolved_timeout_seconds
    assert LLMSettings(provider=LLMProviderKind.OPENAI_COMPATIBLE).resolved_reasoning_effort is None


# ---------------------------------------------------------------- switch


def test_switch_cannot_be_turned_on_without_a_provider() -> None:
    switch = _switch(None)
    assert not switch.status().configured
    assert not switch.enabled
    with pytest.raises(DomainRuleViolationError):
        switch.set_enabled(True)


async def test_switch_off_blocks_calls_and_on_delegates() -> None:
    inner = ScriptedLLM(IntentExtraction(survey_type="employee_engagement"))
    switch = _switch(inner, enabled=False)
    with pytest.raises(LLMUnavailableError, match="switched off"):
        await switch.generate_structured(system="s", user="u", output_type=IntentExtraction)
    assert inner.calls == 0

    assert switch.set_enabled(True).enabled
    result = await switch.generate_structured(system="s", user="u", output_type=IntentExtraction)
    assert result.survey_type == "employee_engagement"


# ---------------------------------------------------------------- agents


async def test_intent_agent_uses_llm_when_switched_on(deps: AgentDependencies) -> None:
    llm = _switch(ScriptedLLM(IntentExtraction(survey_type="compliance_review", objectives=["x"])))
    state = await IntentAgent.create(replace(deps, llm=llm)).run(_state("anything at all"))
    assert state.intent is not None
    assert state.intent.survey_type == "compliance_review"
    assert state.trace[-1].strategy == "llm"


async def test_intent_agent_falls_back_when_switched_off(deps: AgentDependencies) -> None:
    llm = _switch(ScriptedLLM(IntentExtraction(survey_type="compliance_review")), enabled=False)
    state = await IntentAgent.create(replace(deps, llm=llm)).run(_state())
    assert state.intent is not None
    assert state.intent.survey_type == "employee_engagement"  # heuristic classification
    assert state.trace[-1].strategy == "heuristic"
    assert state.trace[-1].note == "fallback: LLM switched off"


async def test_intent_agent_ignores_unknown_llm_survey_type(deps: AgentDependencies) -> None:
    llm = _switch(ScriptedLLM(IntentExtraction(survey_type="made_up_type")))
    state = await IntentAgent.create(replace(deps, llm=llm)).run(_state())
    assert state.intent is not None
    assert state.intent.survey_type == "employee_engagement"


async def _retrieved(deps: AgentDependencies) -> WorkflowState:
    state = await IntentAgent.create(deps).run(_state())
    return await TemplateRetrievalAgent.create(deps).run(state)


async def test_builder_applies_grounded_llm_plan(deps: AgentDependencies) -> None:
    state = await _retrieved(deps)
    ids = [c.template.question.id for c in state.candidates[:6]]
    plan = BuilderPlan(
        title="Burnout Pulse",
        description="d",
        selected_ids=ids,
        rewrites=[LabelRewrite(id=ids[0], label="Reworded question?")],
    )
    builder = QuestionnaireBuilderAgent.create(replace(deps, llm=_switch(ScriptedLLM(plan))))
    built = await builder.run(state)
    assert built.questionnaire is not None
    assert built.trace[-1].strategy == "llm"
    assert built.questionnaire.title == "Burnout Pulse"
    assert "Reworded question?" in [q.label for q in built.questionnaire.questions]


async def test_builder_rejects_ungrounded_plan_and_falls_back(deps: AgentDependencies) -> None:
    state = await _retrieved(deps)
    plan = BuilderPlan(title="Invented", description="d", selected_ids=["nope-1", "nope-2"])
    builder = QuestionnaireBuilderAgent.create(replace(deps, llm=_switch(ScriptedLLM(plan))))
    built = await builder.run(state)
    assert built.questionnaire is not None
    assert built.trace[-1].strategy == "heuristic"
    assert "not grounded" in built.trace[-1].note
    assert len(built.questionnaire.questions) == 6


def test_single_underscore_typo_is_reported() -> None:
    environ = {
        "IFAP_LLM_PROVIDER": "ollama",  # typo: single underscore
        "IFAP_LLM__PROVIDER": "ollama",
        "IFAP_ENVIRONMENT": "local",
        "IFAP_WORKFLOW__AGENT_MAX_ATTEMPTS": "2",
        "IFAP_LLM__NOPE": "x",
        "PATH": "/usr/bin",
    }
    assert unrecognised_env_vars(environ) == ["IFAP_LLM_PROVIDER", "IFAP_LLM__NOPE"]
