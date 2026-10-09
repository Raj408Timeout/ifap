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
from ifap.adapters.persistence.schema import create_engine, migrate_with_retries
from ifap.adapters.persistence.sqlalchemy_repository import SqlAlchemyQuestionnaireRepository
from ifap.agents.framework import (
    DEFAULT_REGISTRY,
    AgentDependencies,
    AgentDescriptor,
    AgentRegistry,
    load_plugins,
)
from ifap.application.jobs import GenerationJobService
from ifap.application.llm_switch import SwitchableLLMClient
from ifap.application.ports import (
    EmbeddingProvider,
    KnowledgeProvider,
    LLMControl,
    TemplateSource,
    WorkflowOrchestrator,
)
from ifap.application.services import (
    KnowledgeIngestionService,
    QuestionnaireAssemblyService,
    QuestionnaireGenerationService,
    QuestionnaireService,
)
from ifap.config.settings import (
    EmbeddingProviderKind,
    KnowledgeProviderKind,
    Settings,
)
from ifap.domain.intent import IntentTaxonomy
from ifap.orchestration.langgraph_orchestrator import LangGraphWorkflowOrchestrator


@dataclass(frozen=True, slots=True)
class Container:  # pylint: disable=too-many-instance-attributes  # composition root: one field per service
    settings: Settings
    engine: AsyncEngine
    knowledge: KnowledgeProvider
    template_source: TemplateSource
    taxonomy: IntentTaxonomy
    generator: QuestionnaireGenerationService
    jobs: GenerationJobService
    questionnaires: QuestionnaireService
    assembly: QuestionnaireAssemblyService
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


def build_orchestrators(
    settings: Settings, deps: AgentDependencies, registry: AgentRegistry
) -> dict[str, WorkflowOrchestrator]:
    """One compiled LangGraph per named workflow in configuration."""
    load_plugins(settings.workflow.plugin_modules)
    return {
        name: LangGraphWorkflowOrchestrator(
            [registry.create(step, deps) for step in definition.steps],
            name=name,
            routes=definition.routes,
            max_visits=definition.max_visits_per_step,
        )
        for name, definition in settings.workflow.workflows.items()
    }


async def build_container(
    settings: Settings, registry: AgentRegistry = DEFAULT_REGISTRY
) -> Container:
    engine = create_engine(settings.database)
    await migrate_with_retries(engine, settings.database)
    events = InMemoryEventBus()
    repository = SqlAlchemyQuestionnaireRepository(engine)
    llm = build_llm(settings)
    knowledge = build_knowledge(settings, build_embeddings(settings))
    taxonomy = load_taxonomy(settings.workflow.taxonomy_path)
    deps = AgentDependencies(
        llm=llm, knowledge=knowledge, workflow=settings.workflow, taxonomy=taxonomy
    )
    orchestrators = build_orchestrators(settings, deps, registry)
    generator = QuestionnaireGenerationService(
        orchestrators=orchestrators,
        default_workflow=settings.workflow.default_workflow,
        repository=repository,
        events=events,
    )
    return Container(
        settings=settings,
        engine=engine,
        knowledge=knowledge,
        template_source=JsonTemplateSource(settings.knowledge.dataset_path),
        taxonomy=taxonomy,
        generator=generator,
        jobs=GenerationJobService(generator, max_jobs=settings.mcp.max_jobs),
        questionnaires=QuestionnaireService(repository=repository, events=events),
        assembly=QuestionnaireAssemblyService(
            knowledge=knowledge, repository=repository, events=events
        ),
        ingestion=KnowledgeIngestionService(
            knowledge=knowledge, events=events, batch_size=settings.knowledge.ingest_batch_size
        ),
        agent_descriptors=tuple(registry.descriptors()),
        llm=llm,
    )
