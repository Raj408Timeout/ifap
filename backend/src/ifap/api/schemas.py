"""HTTP contracts (API DTOs). Kept separate from the domain so the API can version
independently; domain value objects are embedded where their shape *is* the contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from ifap.agents.framework import AgentDescriptor
from ifap.application.workflow import AgentTrace, GenerationOutcome
from ifap.domain.intent import BusinessIntent
from ifap.domain.knowledge import RetrievedTemplate
from ifap.domain.questionnaire import Question, Questionnaire
from ifap.domain.validation import ValidationIssue, ValidationReport


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateQuestionnaireRequest(_Schema):
    message: str = Field(
        min_length=3,
        max_length=4000,
        examples=["Create a 12 question customer satisfaction survey for our online store"],
    )
    question_count: int | None = Field(default=None, ge=1, le=100)
    survey_type: str | None = None
    workflow: str | None = Field(default=None, examples=["standard", "autonomous"])


class ValidationSummary(_Schema):
    is_valid: bool
    issues: list[ValidationIssue]

    @classmethod
    def from_report(cls, report: ValidationReport) -> ValidationSummary:
        return cls(is_valid=report.is_valid, issues=list(report.issues))


class GenerateQuestionnaireResponse(_Schema):
    questionnaire: Questionnaire
    intent: BusinessIntent
    validation: ValidationSummary
    trace: list[AgentTrace]
    source_count: int
    workflow: str
    artifacts: dict[str, SerializeAsAny[BaseModel]]

    @classmethod
    def from_outcome(cls, outcome: GenerationOutcome) -> GenerateQuestionnaireResponse:
        return cls(
            questionnaire=outcome.questionnaire,
            intent=outcome.intent,
            validation=ValidationSummary.from_report(outcome.validation),
            trace=list(outcome.trace),
            source_count=outcome.source_count,
            workflow=outcome.workflow,
            artifacts=dict(outcome.artifacts),
        )


class ReviseQuestionnaireRequest(_Schema):
    title: str = Field(min_length=3)
    description: str = ""
    questions: list[Question]


class QuestionnaireWithValidation(_Schema):
    questionnaire: Questionnaire
    validation: ValidationSummary


class KnowledgeSearchResponse(_Schema):
    results: list[RetrievedTemplate]


class IngestionResponse(_Schema):
    indexed: int
    total: int


class SetLLMRequest(_Schema):
    enabled: bool


class AgentsResponse(_Schema):
    workflows: dict[str, list[str]]
    default_workflow: str
    llm_enabled: bool
    agents: list[AgentDescriptor]


class HealthResponse(_Schema):
    status: str
    environment: str
    knowledge_documents: int


class ErrorResponse(_Schema):
    error: str
    detail: str
