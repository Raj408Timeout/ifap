"""Shared helpers for translating a `KnowledgeQuery` into metadata filters."""

from __future__ import annotations

from ifap.domain.knowledge import KnowledgeQuery, QuestionTemplate


def metadata_filters(query: KnowledgeQuery) -> dict[str, str]:
    candidates = {
        "survey_type": query.survey_type,
        "business_function": query.business_function,
        "industry": query.industry,
    }
    return {key: value for key, value in candidates.items() if value is not None}


def flat_metadata(template: QuestionTemplate) -> dict[str, str]:
    meta = template.metadata
    return {
        "template_id": template.template_id,
        "template_name": template.template_name,
        "survey_type": meta.survey_type,
        "business_function": meta.business_function,
        "industry": meta.industry,
        "category": template.question.category,
        "answer_type": template.question.answer_type.value,
        "tags": ",".join(meta.question_tags),
    }


def clamp_score(value: float) -> float:
    return max(0.0, min(1.0, value))
