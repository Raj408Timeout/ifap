"""Composition root: the ONLY place that knows concrete adapter classes.

Everything else receives its collaborators through constructors (dependency injection).
Choosing Chroma vs. in-memory, OpenAI vs. heuristic, Postgres vs. SQLite happens here, from
configuration.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from ifap.adapters.events.in_memory_bus import InMemoryEventBus
from ifap.adapters.knowledge.chroma_provider import ChromaKnowledgeProvider, create_chroma_client
from ifap.adapters.knowledge.embeddings import (
    HashingEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from ifap.adapters.knowledge.in_memory_provider import InMemoryKnowledgeProvider
from ifap.adapters.knowledge.json_source import JsonTemplateSource, load_taxonomy
from ifap.adapters.llm.openai_compatible import OpenAICompatibleLLMClient
from ifap.adapters.persistence.sqlalchemy_repository import (
    SqlAlchemyQuestionnaireRepository,
    create_engine,
    create_schema,
)
from ifap.agents.framework import (
    DEFAULT_REGISTRY,
    AgentDependencies,
    AgentDescriptor,
    AgentRegistry,
    load_plugins,
)
from ifap.application.llm_switch import SwitchableLLMClient
from ifap.application.ports import (
    EmbeddingProvider,
    KnowledgeProvider,
    LLMControl,
    QuestionnaireGenerator,
    TemplateSource,
    WorkflowOrchestrator,
)
from ifap.application.services import (
    KnowledgeIngestionService,
    QuestionnaireGenerationService,
    QuestionnaireService,
)
from ifap.config.settings import (
    EmbeddingProviderKind,
    KnowledgeProviderKind,
    Settings,
)
from ifap.orchestration.langgraph_orchestrator import LangGraphWorkflowOrchestrator


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    knowledge: KnowledgeProvider
    template_source: TemplateSource
    orchestrator: WorkflowOrchestrator
    generator: QuestionnaireGenerator
    questionnaires: QuestionnaireService
    ingestion: KnowledgeIngestionService
    agent_descriptors: tuple[AgentDescriptor, ...]
    llm: LLMControl


def build_llm(settings: Settings) -> SwitchableLLMClient:
    """Wrap the configured provider in a runtime switch; agents fall back to heuristics when off."""
    llm = settings.llm
    inner = OpenAICompatibleLLMClient(llm) if llm.configured else None
    return SwitchableLLMClient(
        inner,
        provider=llm.kind.value,
        model=llm.resolved_model if llm.configured else None,
        enabled=llm.enabled,
    )


def build_embeddings(settings: Settings) -> EmbeddingProvider:
    if settings.embedding.provider is EmbeddingProviderKind.OPENAI_COMPATIBLE:
        return OpenAICompatibleEmbeddingProvider(settings.embedding, settings.llm)
    return HashingEmbeddingProvider(settings.embedding.dimension)


def build_knowledge(settings: Settings, embeddings: EmbeddingProvider) -> KnowledgeProvider:
    if settings.knowledge.provider is KnowledgeProviderKind.IN_MEMORY:
        return InMemoryKnowledgeProvider(embeddings)
    return ChromaKnowledgeProvider(
        client=create_chroma_client(settings.knowledge),
        collection=settings.knowledge.collection,
        embeddings=embeddings,
    )


def build_orchestrator(
    settings: Settings, deps: AgentDependencies, registry: AgentRegistry
) -> WorkflowOrchestrator:
    load_plugins(settings.workflow.plugin_modules)
    agents = [registry.create(name, deps) for name in settings.workflow.pipeline]
    return LangGraphWorkflowOrchestrator(agents)


async def build_container(
    settings: Settings, registry: AgentRegistry = DEFAULT_REGISTRY
) -> Container:
    engine = create_engine(settings.database)
    await create_schema(engine)
    events = InMemoryEventBus()
    repository = SqlAlchemyQuestionnaireRepository(engine)
    llm = build_llm(settings)
    knowledge = build_knowledge(settings, build_embeddings(settings))
    deps = AgentDependencies(
        llm=llm,
        knowledge=knowledge,
        workflow=settings.workflow,
        taxonomy=load_taxonomy(settings.workflow.taxonomy_path),
    )
    orchestrator = build_orchestrator(settings, deps, registry)
    return Container(
        settings=settings,
        engine=engine,
        knowledge=knowledge,
        template_source=JsonTemplateSource(settings.knowledge.dataset_path),
        orchestrator=orchestrator,
        generator=QuestionnaireGenerationService(
            orchestrator=orchestrator, repository=repository, events=events
        ),
        questionnaires=QuestionnaireService(repository=repository, events=events),
        ingestion=KnowledgeIngestionService(
            knowledge=knowledge, events=events, batch_size=settings.knowledge.ingest_batch_size
        ),
        agent_descriptors=tuple(registry.descriptors()),
        llm=llm,
    )
