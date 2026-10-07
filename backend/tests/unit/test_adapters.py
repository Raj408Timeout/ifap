"""Adapters and infrastructure helpers, tested offline with fakes at the third-party boundary."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from pydantic import BaseModel, SecretStr

from ifap.adapters.events.in_memory_bus import InMemoryEventBus
from ifap.adapters.knowledge import chroma_provider
from ifap.adapters.knowledge.chroma_provider import ChromaKnowledgeProvider, create_chroma_client
from ifap.adapters.knowledge.embeddings import (
    HashingEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from ifap.adapters.knowledge.in_memory_provider import InMemoryKnowledgeProvider
from ifap.adapters.llm.openai_compatible import DisabledLLMClient, OpenAICompatibleLLMClient
from ifap.api.container import build_embeddings, build_knowledge
from ifap.application.ports import LLMUnavailableError
from ifap.config.settings import (
    EmbeddingProviderKind,
    EmbeddingSettings,
    KnowledgeProviderKind,
    KnowledgeSettings,
    LLMProviderKind,
    LLMSettings,
    ObservabilitySettings,
    Settings,
    get_settings,
)
from ifap.domain.events import DomainEvent, KnowledgeIngested
from ifap.domain.knowledge import KnowledgeQuery, QuestionTemplate
from ifap.observability import telemetry

# ---------------------------------------------------------------- events


async def test_event_bus_delivers_and_isolates_failing_handlers() -> None:
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []

    async def record(event: DomainEvent) -> None:
        received.append(event)

    async def broken(event: DomainEvent) -> None:
        raise RuntimeError(f"handler bug for {event.name}")

    bus.subscribe(KnowledgeIngested, broken)
    bus.subscribe(KnowledgeIngested, record)
    event = KnowledgeIngested(source="test", document_count=1)
    await bus.publish(event)
    assert received == [event]
    assert bus.published == [event]


# ---------------------------------------------------------------- embeddings


async def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashingEmbeddingProvider(32)
    [first, second] = await embedder.embed(["patient allergies", "patient allergies"])
    assert embedder.dimension == 32
    assert first == second
    assert math.isclose(sum(value * value for value in first), 1.0)


class FakeEmbeddingsClient:
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


async def test_openai_embedding_adapter_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        EmbeddingSettings(provider=EmbeddingProviderKind.OPENAI_COMPATIBLE, dimension=16),
        LLMSettings(api_key=SecretStr("sk-test")),
    )
    monkeypatch.setattr(provider, "_client", FakeEmbeddingsClient())
    assert provider.dimension == 16
    assert await provider.embed(["abc"]) == [[3.0]]


def test_container_selects_embedding_and_knowledge_providers() -> None:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        embedding=EmbeddingSettings(provider=EmbeddingProviderKind.OPENAI_COMPATIBLE),
        knowledge=KnowledgeSettings(provider=KnowledgeProviderKind.IN_MEMORY),
    )
    embeddings = build_embeddings(settings)
    assert isinstance(embeddings, OpenAICompatibleEmbeddingProvider)
    assert isinstance(build_knowledge(settings, embeddings), InMemoryKnowledgeProvider)


# ---------------------------------------------------------------- knowledge providers


async def test_in_memory_provider_counts(knowledge: InMemoryKnowledgeProvider) -> None:
    assert await knowledge.count() == 200


async def test_chroma_provider_upsert_search_and_filters(
    tmp_path: Path, templates: list[QuestionTemplate]
) -> None:
    provider = ChromaKnowledgeProvider(
        client=create_chroma_client(KnowledgeSettings(chroma_path=tmp_path)),
        collection="test",
        embeddings=HashingEmbeddingProvider(64),
    )
    assert await provider.upsert([]) == 0
    assert await provider.upsert(templates[:80]) == 80
    assert await provider.count() == 80

    unfiltered = await provider.search(KnowledgeQuery(text="manager feedback", top_k=5))
    assert len(unfiltered) == 5

    query = KnowledgeQuery(
        text="manager",
        top_k=50,
        survey_type="employee_engagement",
        business_function="Human Resources",
    )
    combined = await provider.search(query)  # two filters -> Chroma `$and` clause
    assert combined
    assert all(r.template.metadata.business_function == "Human Resources" for r in combined)


def test_chroma_uses_http_client_when_host_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    calls: dict[str, object] = {}

    def fake_http_client(*, host: str, port: int) -> object:
        calls.update(host=host, port=port)
        return sentinel

    monkeypatch.setattr(chroma_provider.chromadb, "HttpClient", fake_http_client)
    client = create_chroma_client(KnowledgeSettings(chroma_host="chroma", chroma_port=8001))
    assert client is sentinel
    assert calls == {"host": "chroma", "port": 8001}


# ---------------------------------------------------------------- LLM adapters


class Answer(BaseModel):
    value: str


class FakeRunnable:
    def __init__(self, *, result: object = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    async def ainvoke(self, messages: object) -> object:
        del messages
        if self._error is not None:
            raise self._error
        return self._result


class FakeChat:
    def __init__(self, runnable: FakeRunnable) -> None:
        self._runnable = runnable

    def with_structured_output(self, schema: object) -> FakeRunnable:
        del schema
        return self._runnable


def _llm_client(
    monkeypatch: pytest.MonkeyPatch, runnable: FakeRunnable
) -> OpenAICompatibleLLMClient:
    client = OpenAICompatibleLLMClient(LLMSettings(provider=LLMProviderKind.OLLAMA))
    monkeypatch.setattr(client, "_chat", FakeChat(runnable))
    return client


async def test_llm_adapter_returns_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _llm_client(monkeypatch, FakeRunnable(result=Answer(value="ok")))
    assert client.enabled
    result = await client.generate_structured(system="s", user="u", output_type=Answer)
    assert result == Answer(value="ok")


async def test_llm_adapter_rejects_unexpected_output_type(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _llm_client(monkeypatch, FakeRunnable(result={"value": "raw dict"}))
    with pytest.raises(LLMUnavailableError, match="Unexpected LLM output: dict"):
        await client.generate_structured(system="s", user="u", output_type=Answer)


async def test_llm_adapter_translates_and_truncates_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _llm_client(monkeypatch, FakeRunnable(error=TimeoutError("x" * 500)))
    with pytest.raises(LLMUnavailableError) as raised:
        await client.generate_structured(system="s", user="u", output_type=Answer)
    assert str(raised.value).startswith("TimeoutError: xxx")
    assert len(str(raised.value)) <= 160


async def test_disabled_llm_client_always_unavailable() -> None:
    client = DisabledLLMClient()
    assert not client.enabled
    with pytest.raises(LLMUnavailableError):
        await client.generate_structured(system="s", user="u", output_type=Answer)


# ---------------------------------------------------------------- settings & telemetry


def test_explicit_llm_settings_override_provider_defaults() -> None:
    settings = LLMSettings(
        provider=LLMProviderKind.OLLAMA,
        model="llama3.1:8b",
        timeout_seconds=30,
        api_key=SecretStr("custom"),
    )
    assert settings.resolved_model == "llama3.1:8b"
    assert settings.resolved_timeout_seconds == 30
    assert settings.resolved_api_key == SecretStr("custom")


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_console_tracing_installs_sdk_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[object] = []
    monkeypatch.setattr(telemetry.trace, "get_tracer_provider", object)
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", installed.append)
    telemetry.configure_tracing(ObservabilitySettings(console_traces=True))
    assert len(installed) == 1
    assert isinstance(installed[0], TracerProvider)
