from __future__ import annotations

from typing import Self

import pytest

from ifap.adapters.knowledge.in_memory_provider import InMemoryKnowledgeProvider
from ifap.agents.builtin.builder_agent import QuestionnaireBuilderAgent, select_diverse
from ifap.agents.builtin.intent_agent import IntentAgent
from ifap.agents.builtin.retrieval_agent import TemplateRetrievalAgent
from ifap.agents.builtin.validation_agent import ValidationAgent, repair
from ifap.agents.framework import (
    AgentDependencies,
    AgentDescriptor,
    AgentOutcome,
    AgentRegistry,
    BaseAgent,
    agent_plugin,
)
from ifap.application.workflow import AgentStatus, GenerationRequest, WorkflowState
from ifap.domain.errors import AgentNotRegisteredError
from ifap.domain.knowledge import KnowledgeQuery
from ifap.domain.questionnaire import AnswerType, DependencyRule, Question, Questionnaire
from ifap.domain.validation import validate_questionnaire


def _state(message: str, **kwargs: int | str) -> WorkflowState:
    return WorkflowState(request=GenerationRequest.model_validate({"message": message, **kwargs}))


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Survey our online store customers about delivery and support", "customer_satisfaction"),
        ("Quarterly pulse to measure employee burnout and manager quality", "employee_engagement"),
        ("New patient intake form for a cardiology clinic", "healthcare_assessment"),
        ("Collect feedback on the beta release of our SaaS app", "product_feedback"),
        ("Annual GDPR and vendor risk compliance audit", "compliance_review"),
    ],
)
async def test_intent_agent_classifies(
    deps: AgentDependencies, message: str, expected: str
) -> None:
    state = await IntentAgent.create(deps).run(_state(message))
    assert state.intent is not None
    assert state.intent.survey_type == expected
    assert state.trace[-1].strategy == "heuristic"


async def test_intent_agent_extracts_and_caps_question_count(deps: AgentDependencies) -> None:
    agent = IntentAgent.create(deps)
    small = await agent.run(_state("Create 7 questions for customers"))
    huge = await agent.run(_state("Create 999 questions for customers"))
    assert small.intent is not None
    assert small.intent.question_count == 7
    assert huge.intent is not None
    assert huge.intent.question_count == deps.workflow.max_question_count


async def test_retrieval_filters_by_survey_type(deps: AgentDependencies) -> None:
    state = await IntentAgent.create(deps).run(_state("employee wellbeing and burnout survey"))
    state = await TemplateRetrievalAgent.create(deps).run(state)
    assert state.candidates
    assert {c.template.metadata.survey_type for c in state.candidates} == {"employee_engagement"}


async def test_knowledge_ranks_semantically_close_question_first(
    knowledge: InMemoryKnowledgeProvider,
) -> None:
    results = await knowledge.search(KnowledgeQuery(text="medication allergies", top_k=3))
    assert "allerg" in results[0].template.question.label.lower()


async def test_builder_respects_count_and_keeps_skip_logic_parents(deps: AgentDependencies) -> None:
    state = _state("Customer satisfaction survey about support and returns", question_count=12)
    for agent in (IntentAgent, TemplateRetrievalAgent, QuestionnaireBuilderAgent):
        state = await agent.create(deps).run(state)
    assert state.questionnaire is not None
    questionnaire = state.questionnaire
    assert len(questionnaire.questions) == 12
    assert validate_questionnaire(questionnaire).is_valid
    assert len({q.category for q in questionnaire.questions}) >= 6


async def test_select_diverse_never_exceeds_count(knowledge: InMemoryKnowledgeProvider) -> None:
    candidates = await knowledge.search(KnowledgeQuery(text="patient symptoms", top_k=40))
    for count in (1, 2, 5, 15):
        assert len(select_diverse(candidates, count)) <= count


def test_validation_repair_removes_dangling_rules_and_duplicates() -> None:
    orphan = Question(
        id="q2",
        label="Follow up?",
        category="c",
        answer_type=AnswerType.BOOLEAN,
        dependency_rules=(DependencyRule(depends_on="missing", value=True),),
    )
    base = Question(id="q1", label="Base?", category="c", answer_type=AnswerType.BOOLEAN)
    questionnaire = Questionnaire(title="Test", survey_type="x", questions=(base, orphan, base))
    repaired, repairs = repair(questionnaire)
    assert repairs == 2
    assert repaired.question_ids() == ["q1", "q2"]
    assert repaired.questions[1].dependency_rules == ()


async def test_validation_agent_halts_without_questionnaire(deps: AgentDependencies) -> None:
    state = await ValidationAgent.create(deps).run(_state("anything at all"))
    assert state.halted
    assert state.trace[-1].status is AgentStatus.FAILED
    assert state.trace[-1].attempts == deps.workflow.agent_max_attempts


class _FlakyAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(max_attempts=3, backoff_seconds=0.0)
        self.calls = 0

    @classmethod
    def create(cls, deps: AgentDependencies) -> Self:
        del deps
        return cls()

    async def _execute(self, state: WorkflowState) -> AgentOutcome:
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("transient")
        return AgentOutcome(state=state)


async def test_base_agent_retries_until_success(deps: AgentDependencies) -> None:
    registry = AgentRegistry()
    agent_plugin(AgentDescriptor(name="flaky", version="0.1.0", description="test"), registry)(
        _FlakyAgent
    )
    agent = registry.create("flaky", deps)
    state = await agent.run(_state("hello world"))
    assert not state.halted
    assert state.trace[-1].attempts == 3


def test_registry_rejects_unknown_agent(deps: AgentDependencies) -> None:
    with pytest.raises(AgentNotRegisteredError):
        AgentRegistry().create("ghost", deps)
