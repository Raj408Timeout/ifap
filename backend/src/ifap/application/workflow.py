"""The shared state that flows through the agent workflow, plus request/outcome DTOs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from ifap.domain.intent import BusinessIntent
from ifap.domain.knowledge import RetrievedTemplate
from ifap.domain.questionnaire import Questionnaire
from ifap.domain.validation import ValidationReport


class GenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = Field(min_length=3, max_length=4000)
    question_count: int | None = Field(default=None, ge=1, le=100)
    survey_type: str | None = None
    workflow: str | None = Field(default=None, description="Named workflow; default if omitted")


class AgentStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentTrace(BaseModel):
    """One row of the execution trace, returned to clients for transparency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    version: str
    status: AgentStatus
    attempts: int
    duration_ms: float
    strategy: str = ""
    note: str = ""


class WorkflowState(BaseModel):
    """Blackboard shared by agents. Each agent reads what it needs and returns a new copy.

    * Core fields (`intent`, `candidates`, ...) are the questionnaire-generation contract.
    * `artifacts` gives every agent its own named slot, so new agents add outputs without
      editing this class (Open/Closed).
    * `signal` is an agent's routing outcome (e.g. "invalid"); the *workflow definition*
      maps signals to next steps, so agents never hard-code the graph.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=False)

    request: GenerationRequest
    intent: BusinessIntent | None = None
    candidates: tuple[RetrievedTemplate, ...] = ()
    questionnaire: Questionnaire | None = None
    validation: ValidationReport | None = None
    trace: tuple[AgentTrace, ...] = ()
    artifacts: dict[str, SerializeAsAny[BaseModel]] = Field(default_factory=dict[str, BaseModel])
    signal: str | None = None
    halted: bool = False
    halt_reason: str | None = None

    def with_artifact(self, key: str, value: BaseModel) -> WorkflowState:
        return self.model_copy(update={"artifacts": {**self.artifacts, key: value}})

    def artifact[T: BaseModel](self, key: str, kind: type[T]) -> T | None:
        value = dict(self.artifacts).get(key)
        return value if isinstance(value, kind) else None

    def with_signal(self, signal: str | None) -> WorkflowState:
        return self.model_copy(update={"signal": signal})

    def visits(self, agent: str) -> int:
        return sum(1 for entry in self.trace if entry.agent == agent)

    def with_trace(self, entry: AgentTrace) -> WorkflowState:
        return self.model_copy(update={"trace": (*self.trace, entry)})

    def halt(self, reason: str) -> WorkflowState:
        return self.model_copy(update={"halted": True, "halt_reason": reason})


class GenerationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    questionnaire: Questionnaire
    intent: BusinessIntent
    validation: ValidationReport
    trace: tuple[AgentTrace, ...]
    source_count: int
    workflow: str
    artifacts: dict[str, SerializeAsAny[BaseModel]] = Field(default_factory=dict[str, BaseModel])
