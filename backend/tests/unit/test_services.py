"""Use cases, the agent framework registry/plugins, and agent guard clauses."""

from __future__ import annotations

from pathlib import Path
from typing import Self
from uuid import uuid4

import pytest

from ifap.adapters.events.in_memory_bus import InMemoryEventBus
from ifap.adapters.knowledge.in_memory_provider import InMemoryKnowledgeProvider
from ifap.adapters.knowledge.json_source import JsonTemplateSource
from ifap.adapters.persistence.schema import create_engine, migrate
from ifap.adapters.persistence.sqlalchemy_repository import SqlAlchemyQuestionnaireRepository
from ifap.agents import framework
from ifap.agents.builtin.builder_agent import QuestionnaireBuilderAgent, select_diverse
from ifap.agents.builtin.retrieval_agent import TemplateRetrievalAgent
from ifap.agents.framework import (
    DEFAULT_REGISTRY,
    AgentDependencies,
    AgentDescriptor,
    AgentOutcome,
    AgentRegistry,
    BaseAgent,
    load_plugins,
)
from ifap.application.services import (
    KnowledgeIngestionService,
    QuestionnaireGenerationService,
)
from ifap.application.workflow import AgentStatus, GenerationRequest, WorkflowState
from ifap.config.settings import DatabaseSettings, KnowledgeSettings
from ifap.domain.errors import AgentExecutionError
from ifap.domain.intent import BusinessIntent
from ifap.domain.knowledge import KnowledgeQuery
from ifap.domain.questionnaire import AnswerType, Question, Questionnaire
from ifap.orchestration.langgraph_orchestrator import LangGraphWorkflowOrchestrator

# ---------------------------------------------------------------- generation use case


def _service(deps: AgentDependencies, pipeline: list[str]) -> QuestionnaireGenerationService:
    load_plugins(["ifap.agents.builtin"])
    orchestrator = LangGraphWorkflowOrchestrator(
        [DEFAULT_REGISTRY.create(n, deps) for n in pipeline]
    )
    return QuestionnaireGenerationService(
        orchestrators={"standard": orchestrator},
        default_workflow="standard",
        repository=_UnusedRepository(),
        events=InMemoryEventBus(),
    )


class _UnusedRepository:
    async def save(self, questionnaire: Questionnaire) -> None:
        raise AssertionError(f"failed workflows must not be saved: {questionnaire.id}")

    async def get(self, questionnaire_id: object) -> Questionnaire | None:
        raise AssertionError(questionnaire_id)

    async def list(self, *, limit: int, offset: int) -> list[Questionnaire]:
        raise AssertionError((limit, offset))


async def test_generation_raises_when_an_agent_halts(deps: AgentDependencies) -> None:
    service = _service(deps, ["intent", "questionnaire_builder"])  # no retrieval -> builder halts
    with pytest.raises(AgentExecutionError, match="questionnaire_builder"):
        await service.generate(GenerationRequest(message="customer survey"))


async def test_generation_raises_when_pipeline_builds_nothing(deps: AgentDependencies) -> None:
    service = _service(deps, ["intent"])
    with pytest.raises(AgentExecutionError, match="without a validated questionnaire"):
        await service.generate(GenerationRequest(message="customer survey"))


def test_orchestrator_requires_agents() -> None:
    with pytest.raises(ValueError, match="at least one agent"):
        LangGraphWorkflowOrchestrator([])


# ---------------------------------------------------------------- ingestion & persistence


async def test_ensure_seeded_skips_populated_knowledge_base(
    knowledge: InMemoryKnowledgeProvider,
) -> None:
    service = KnowledgeIngestionService(
        knowledge=knowledge, events=InMemoryEventBus(), batch_size=50
    )
    source = JsonTemplateSource(KnowledgeSettings().dataset_path)
    assert await service.ensure_seeded(source) == 0


def _questionnaire(title: str) -> Questionnaire:
    question = Question(id="q1", label="Yes or no?", category="c", answer_type=AnswerType.BOOLEAN)
    return Questionnaire(title=title, survey_type="x", questions=(question,))


async def test_sqlalchemy_repository_round_trip_and_listing(tmp_path: Path) -> None:
    engine = create_engine(DatabaseSettings(url=f"sqlite+aiosqlite:///{tmp_path / 'repo.db'}"))
    await migrate(engine)
    repository = SqlAlchemyQuestionnaireRepository(engine)
    first, second = _questionnaire("First survey"), _questionnaire("Second survey")
    await repository.save(first)
    await repository.save(second)

    assert await repository.get(first.id) == first
    assert await repository.get(uuid4()) is None
    listed = await repository.list(limit=1, offset=0)
    assert [q.title for q in listed] == ["Second survey"]  # newest first
    assert len(await repository.list(limit=10, offset=1)) == 1
    await engine.dispose()


# ---------------------------------------------------------------- agent framework


class _NoopAgent(BaseAgent):
    @classmethod
    def create(cls, deps: AgentDependencies) -> Self:
        del deps
        return cls(max_attempts=1, backoff_seconds=0.0)

    async def _execute(self, state: WorkflowState) -> AgentOutcome:
        return AgentOutcome(state=state)


def test_registry_register_unregister_and_conflicts() -> None:
    registry = AgentRegistry()
    v1 = AgentDescriptor(name="noop", version="1.0.0", description="test")
    registry.register(v1, _NoopAgent.create)
    registry.register(v1, _NoopAgent.create)  # idempotent for an identical descriptor
    assert "noop" in registry
    with pytest.raises(ValueError, match="already registered"):
        registry.register(v1.model_copy(update={"version": "2.0.0"}), _NoopAgent.create)
    registry.unregister("noop")
    assert "noop" not in registry
    assert not registry.descriptors()


class _FakeEntryPoint:
    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> None:
        self.loaded = True


def test_load_plugins_loads_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _FakeEntryPoint()

    def fake_entry_points(*, group: str) -> list[_FakeEntryPoint]:
        return [entry_point] if group == framework.ENTRY_POINT_GROUP else []

    monkeypatch.setattr(framework, "entry_points", fake_entry_points)
    load_plugins([])
    assert entry_point.loaded


# ---------------------------------------------------------------- agent guard clauses & branches


async def test_retrieval_and_builder_halt_without_intent(deps: AgentDependencies) -> None:
    state = WorkflowState(request=GenerationRequest(message="customer survey"))
    for agent in (TemplateRetrievalAgent.create(deps), QuestionnaireBuilderAgent.create(deps)):
        result = await agent.run(state)
        assert result.halted
        assert result.trace[-1].status is AgentStatus.FAILED


async def test_retrieval_broadens_when_filtered_results_are_too_few(
    deps: AgentDependencies,
) -> None:
    intent = BusinessIntent(
        raw_request="employee survey", survey_type="employee_engagement", question_count=45
    )
    request = GenerationRequest(message="employee survey")
    state = WorkflowState(request=request, intent=intent)  # only 40 employee templates exist
    result = await TemplateRetrievalAgent.create(deps).run(state)
    assert result.trace[-1].strategy == "broadened"
    assert len({c.template.metadata.survey_type for c in result.candidates}) > 1


async def test_select_diverse_returns_all_when_count_exceeds_candidates(
    knowledge: InMemoryKnowledgeProvider,
) -> None:
    candidates = await knowledge.search(KnowledgeQuery(text="audit evidence", top_k=3))
    selected = select_diverse(candidates, count=50)
    assert 0 < len(selected) <= 3
