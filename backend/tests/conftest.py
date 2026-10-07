# pylint: disable=redefined-outer-name  # pytest fixtures are injected by name
from __future__ import annotations

from pathlib import Path

import pytest

from ifap.adapters.knowledge.embeddings import HashingEmbeddingProvider
from ifap.adapters.knowledge.in_memory_provider import InMemoryKnowledgeProvider
from ifap.adapters.knowledge.json_source import JsonTemplateSource, load_taxonomy
from ifap.adapters.llm.openai_compatible import DisabledLLMClient
from ifap.agents.framework import AgentDependencies
from ifap.config.settings import (
    DatabaseSettings,
    KnowledgeProviderKind,
    KnowledgeSettings,
    LLMSettings,
    ObservabilitySettings,
    Settings,
    WorkflowSettings,
)
from ifap.domain.intent import IntentTaxonomy
from ifap.domain.knowledge import QuestionTemplate


@pytest.fixture
def workflow_settings() -> WorkflowSettings:
    return WorkflowSettings(agent_retry_backoff_seconds=0.0)


@pytest.fixture(scope="session")
def templates() -> list[QuestionTemplate]:
    return JsonTemplateSource(KnowledgeSettings().dataset_path).load()


@pytest.fixture(scope="session")
def taxonomy() -> IntentTaxonomy:
    return load_taxonomy(WorkflowSettings().taxonomy_path)


@pytest.fixture
async def knowledge(templates: list[QuestionTemplate]) -> InMemoryKnowledgeProvider:
    provider = InMemoryKnowledgeProvider(HashingEmbeddingProvider(384))
    await provider.upsert(templates)
    return provider


@pytest.fixture
def deps(
    knowledge: InMemoryKnowledgeProvider,
    workflow_settings: WorkflowSettings,
    taxonomy: IntentTaxonomy,
) -> AgentDependencies:
    return AgentDependencies(
        llm=DisabledLLMClient(), knowledge=knowledge, workflow=workflow_settings, taxonomy=taxonomy
    )


@pytest.fixture
def app_settings(tmp_path: Path) -> Settings:
    # Tests never read backend/.env: a developer's local LLM must not leak into the suite.
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        llm=LLMSettings(),
        database=DatabaseSettings(url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"),
        knowledge=KnowledgeSettings(
            provider=KnowledgeProviderKind.CHROMA, chroma_path=tmp_path / "chroma"
        ),
        workflow=WorkflowSettings(agent_retry_backoff_seconds=0.0),
        observability=ObservabilitySettings(log_json=False, log_level="WARNING"),
    )
