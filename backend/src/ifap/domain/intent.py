"""The structured interpretation of what the user asked for."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BusinessIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_request: str
    survey_type: str
    business_function: str | None = None
    industry: str | None = None
    target_audience: str | None = None
    objectives: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    question_count: int = Field(default=10, ge=1, le=100)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SurveyTypeProfile(BaseModel):
    """Configured knowledge about a survey type (loaded from `intent_taxonomy.json`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    survey_type: str
    title: str
    business_function: str
    industry: str
    keywords: tuple[str, ...]


class IntentTaxonomy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_survey_type: str
    survey_types: tuple[SurveyTypeProfile, ...]
    stopwords: frozenset[str] = frozenset()

    def profile(self, survey_type: str) -> SurveyTypeProfile | None:
        return next((p for p in self.survey_types if p.survey_type == survey_type), None)
