"""The shared state that flows through the agent workflow, plus request/outcome DTOs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ifap.domain.intent import BusinessIntent
from ifap.domain.knowledge import RetrievedTemplate
from ifap.domain.questionnaire import Questionnaire
from ifap.domain.validation import ValidationReport


class GenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = Field(min_length=3, max_length=4000)
    question_count: int | None = Field(default=None, ge=1, le=100)
    survey_type: str | None = None


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
    """Blackboard shared by agents. Each agent reads what it needs and returns a new copy."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=False)

    request: GenerationRequest
    intent: BusinessIntent | None = None
    candidates: tuple[RetrievedTemplate, ...] = ()
    questionnaire: Questionnaire | None = None
    validation: ValidationReport | None = None
    trace: tuple[AgentTrace, ...] = ()
    halted: bool = False
    halt_reason: str | None = None

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
