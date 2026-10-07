"""Driven ports (secondary ports). The application core depends only on these protocols;
adapters in `ifap.adapters` implement them. Swapping Chroma for Pinecone, or OpenAI for
Azure OpenAI, means writing a new adapter - never editing the core.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel

from ifap.application.workflow import GenerationOutcome, GenerationRequest, WorkflowState
from ifap.domain.events import DomainEvent
from ifap.domain.knowledge import KnowledgeQuery, QuestionTemplate, RetrievedTemplate
from ifap.domain.questionnaire import Questionnaire


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def dimension(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class KnowledgeProvider(Protocol):
    """Isolates the knowledge base (vector store + metadata) from orchestration."""

    async def upsert(self, templates: Sequence[QuestionTemplate]) -> int: ...

    async def search(self, query: KnowledgeQuery) -> list[RetrievedTemplate]: ...

    async def count(self) -> int: ...


class LLMUnavailableError(RuntimeError):
    """Raised when no LLM is configured or the call failed; agents fall back to heuristics."""


@runtime_checkable
class LLMClient(Protocol):
    """Structured-output LLM call. Implementations raise `LLMUnavailableError` on any failure."""

    @property
    def enabled(self) -> bool: ...

    async def generate_structured[T: BaseModel](
        self, *, system: str, user: str, output_type: type[T]
    ) -> T: ...


@runtime_checkable
class QuestionnaireRepository(Protocol):
    async def save(self, questionnaire: Questionnaire) -> None: ...

    async def get(self, questionnaire_id: UUID) -> Questionnaire | None: ...

    async def list(self, *, limit: int, offset: int) -> list[Questionnaire]: ...


class LLMStatus(BaseModel):
    provider: str
    model: str | None
    configured: bool
    enabled: bool


@runtime_checkable
class LLMControl(Protocol):
    """Runtime on/off switch for LLM-backed agent strategies."""

    def status(self) -> LLMStatus: ...

    def set_enabled(self, enabled: bool) -> LLMStatus: ...


EventHandler = Callable[[DomainEvent], Awaitable[None]]


@runtime_checkable
class EventPublisher(Protocol):
    async def publish(self, event: DomainEvent) -> None: ...

    def subscribe(self, event_type: type[DomainEvent], handler: EventHandler) -> None: ...


@runtime_checkable
class WorkflowOrchestrator(Protocol):
    """Runs the configured agent pipeline over a workflow state."""

    @property
    def pipeline(self) -> list[str]: ...

    async def run(self, state: WorkflowState) -> WorkflowState: ...


@runtime_checkable
class QuestionnaireGenerator(Protocol):
    """Driving port: the use case the API (or a chatbot channel) calls."""

    async def generate(self, request: GenerationRequest) -> GenerationOutcome: ...


@runtime_checkable
class TemplateSource(Protocol):
    """Where raw templates come from (JSON file, CMS, SharePoint, S3...)."""

    @property
    def name(self) -> str: ...

    def load(self) -> list[QuestionTemplate]: ...
