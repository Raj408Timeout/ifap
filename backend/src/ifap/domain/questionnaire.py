"""Questionnaire aggregate: the core domain model of IFAP.

The domain layer has no dependency on frameworks other than Pydantic, which is
used purely as a typed, validated value-object toolkit (see ADR-004).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ifap.domain.errors import DomainRuleViolationError


class AnswerType(StrEnum):
    """Supported answer types. Skip logic is modelled by `DependencyRule`."""

    BOOLEAN = "boolean"
    MULTIPLE_CHOICE = "multiple_choice"
    SINGLE_CHOICE = "single_choice"
    RICH_TEXT = "rich_text"
    NUMERIC = "numeric"
    DATE = "date"
    FILE_UPLOAD = "file_upload"


MIN_CHOICES = 2

CHOICE_BASED_TYPES: frozenset[AnswerType] = frozenset(
    {AnswerType.MULTIPLE_CHOICE, AnswerType.SINGLE_CHOICE}
)


class ValidationKind(StrEnum):
    REQUIRED = "required"
    MIN_VALUE = "min_value"
    MAX_VALUE = "max_value"
    MIN_LENGTH = "min_length"
    MAX_LENGTH = "max_length"
    REGEX = "regex"
    ALLOWED_FILE_TYPES = "allowed_file_types"
    MAX_FILE_SIZE_MB = "max_file_size_mb"


class DependencyOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"


class DependencyAction(StrEnum):
    SHOW = "show"
    SKIP = "skip"


class QuestionnaireStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class _ValueObject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Choice(_ValueObject):
    value: str = Field(min_length=1)
    label: str = Field(min_length=1)


class Validation(_ValueObject):
    kind: ValidationKind
    value: str | int | float | bool | list[str] | None = None
    message: str | None = None


class DependencyRule(_ValueObject):
    """Skip logic: this question is shown/skipped based on another answer."""

    depends_on: str = Field(min_length=1)
    operator: DependencyOperator = DependencyOperator.EQUALS
    value: str | int | float | bool
    action: DependencyAction = DependencyAction.SHOW


class Question(_ValueObject):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=3)
    category: str = Field(min_length=1)
    description: str = ""
    answer_type: AnswerType
    choices: tuple[Choice, ...] = ()
    validations: tuple[Validation, ...] = ()
    dependency_rules: tuple[DependencyRule, ...] = ()
    business_tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _choices_match_type(self) -> Self:
        needs_choices = self.answer_type in CHOICE_BASED_TYPES
        if needs_choices and len(self.choices) < MIN_CHOICES:
            raise ValueError(
                f"{self.answer_type} question '{self.id}' needs >= {MIN_CHOICES} choices"
            )
        if not needs_choices and self.choices:
            raise ValueError(f"{self.answer_type} question '{self.id}' must not have choices")
        return self


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Questionnaire(BaseModel):
    """Aggregate root. Mutations return new instances to keep history explicit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=3)
    description: str = ""
    survey_type: str
    status: QuestionnaireStatus = QuestionnaireStatus.DRAFT
    version: int = Field(default=1, ge=1)
    questions: tuple[Question, ...] = ()
    source_template_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def question_ids(self) -> list[str]:
        return [question.id for question in self.questions]

    def revise(self, *, title: str, description: str, questions: tuple[Question, ...]) -> Self:
        self._ensure_editable()
        return self.model_copy(
            update={
                "title": title,
                "description": description,
                "questions": questions,
                "version": self.version + 1,
                "updated_at": _utcnow(),
            }
        )

    def publish(self) -> Self:
        self._ensure_editable()
        if not self.questions:
            raise DomainRuleViolationError("Cannot publish an empty questionnaire")
        return self.model_copy(
            update={"status": QuestionnaireStatus.PUBLISHED, "updated_at": _utcnow()}
        )

    def _ensure_editable(self) -> None:
        if self.status is not QuestionnaireStatus.DRAFT:
            raise DomainRuleViolationError(
                f"Questionnaire {self.id} is {self.status}, not editable"
            )
