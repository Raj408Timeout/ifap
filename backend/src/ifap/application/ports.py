"""Driven ports (secondary ports). The application core depends only on these protocols;
adapters in `ifap.adapters` implement them. Swapping Chroma for Pinecone, or OpenAI for
Azure OpenAI, means writing a new adapter - never editing the core.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

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

    async def get(self, template_ids: Sequence[str]) -> list[QuestionTemplate]:
        """Templates by id, in the requested order; unknown ids are skipped."""
        ...

    async def count(self) -> int: ...


class LLMUnavailableError(RuntimeError):
    """Raised when no LLM is configured or the call failed; agents fall back to heuristics."""


class _LLMValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolSpec(_LLMValue):
    """A tool the model may call. `input_schema` is a JSON Schema object."""

    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str
    input_schema: dict[str, JsonValue]


class ToolCall(_LLMValue):
    id: str
    name: str
    arguments: dict[str, JsonValue]


class ChatMessage(_LLMValue):
    """Provider-neutral conversation turn (LangChain/OpenAI types never leave the adapter)."""

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


class AssistantTurn(_LLMValue):
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


@runtime_checkable
class LLMClient(Protocol):
    """Structured-output LLM call. Implementations raise `LLMUnavailableError` on any failure."""

    @property
    def enabled(self) -> bool: ...

    async def generate_structured[T: BaseModel](
        self, *, system: str, user: str, output_type: type[T]
    ) -> T: ...

    async def converse(
        self, *, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> AssistantTurn:
        """One model turn that may request tool calls (the basis of autonomous agents)."""
        ...

    def model_states(self) -> list[ModelState]:
        """The model chain in priority order, with availability (empty if no models)."""
        ...


@runtime_checkable
class QuestionnaireRepository(Protocol):
    async def save(self, questionnaire: Questionnaire) -> None: ...

    async def get(self, questionnaire_id: UUID) -> Questionnaire | None: ...

    async def list(self, *, limit: int, offset: int) -> list[Questionnaire]: ...


class ModelState(BaseModel):
    """Health of one model in the provider's fallback chain."""

    name: str
    available: bool
    reason: str | None = None
    available_in_seconds: int | None = None  # when a cooldown or daily quota resets


class LLMStatus(BaseModel):
    provider: str
    model: str | None = Field(description="Model the next call will use (first available)")
    configured: bool
    enabled: bool
    models: list[ModelState] = Field(default_factory=list[ModelState])


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


StepObserver = Callable[[WorkflowState], None]
"""Called with the full workflow state after each completed step (progress reporting)."""


@runtime_checkable
class WorkflowOrchestrator(Protocol):
    """Runs one named workflow (an agent graph) over a workflow state."""

    @property
    def name(self) -> str: ...

    @property
    def pipeline(self) -> list[str]: ...

    async def run(
        self, state: WorkflowState, on_step: StepObserver | None = None
    ) -> WorkflowState: ...


@runtime_checkable
class QuestionnaireGenerator(Protocol):
    """Driving port: the use case the API (or a chatbot channel) calls."""

    async def generate(
        self, request: GenerationRequest, on_step: StepObserver | None = None
    ) -> GenerationOutcome: ...


@runtime_checkable
class TemplateSource(Protocol):
    """Where raw templates come from (JSON file, CMS, SharePoint, S3...)."""

    @property
    def name(self) -> str: ...

    def load(self) -> list[QuestionTemplate]: ...
