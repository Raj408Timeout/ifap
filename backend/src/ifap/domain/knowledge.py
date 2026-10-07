"""Knowledge-base value objects: templates stored in, and retrieved from, the RAG layer."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ifap.domain.questionnaire import Question


class TemplateMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    industry: str
    business_function: str
    survey_type: str
    question_tags: tuple[str, ...] = ()


class QuestionTemplate(BaseModel):
    """One reusable question, belonging to a named template (e.g. 'Customer Satisfaction')."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str = Field(min_length=1)
    template_name: str = Field(min_length=1)
    question: Question
    metadata: TemplateMetadata

    def to_document_text(self) -> str:
        """Text that is embedded. Combines the semantic signals a user query would match."""
        choices = ", ".join(choice.label for choice in self.question.choices)
        parts = [
            f"Template: {self.template_name}",
            f"Survey type: {self.metadata.survey_type}",
            f"Business function: {self.metadata.business_function}",
            f"Industry: {self.metadata.industry}",
            f"Category: {self.question.category}",
            f"Question: {self.question.label}",
            f"Description: {self.question.description}",
            f"Choices: {choices}" if choices else "",
            f"Tags: {', '.join(self.metadata.question_tags)}",
        ]
        return "\n".join(part for part in parts if part)


class KnowledgeQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    top_k: int = Field(default=20, ge=1, le=200)
    survey_type: str | None = None
    business_function: str | None = None
    industry: str | None = None


class RetrievedTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template: QuestionTemplate
    score: float = Field(ge=0.0, le=1.0)
