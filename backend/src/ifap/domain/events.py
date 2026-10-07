"""Domain events. Published after state changes so future agents can react asynchronously."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=_utcnow)

    @property
    def name(self) -> str:
        return type(self).__name__


class QuestionnaireGenerated(DomainEvent):
    questionnaire_id: UUID
    survey_type: str
    question_count: int
    is_valid: bool


class QuestionnaireRevised(DomainEvent):
    questionnaire_id: UUID
    version: int


class QuestionnairePublished(DomainEvent):
    questionnaire_id: UUID


class KnowledgeIngested(DomainEvent):
    source: str
    document_count: int
