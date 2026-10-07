"""Intent Agent - turns a free-text business need into a structured `BusinessIntent`.

Strategy: ask the LLM for structured output; if no LLM is configured (or it fails), fall back
to a deterministic keyword classifier driven by `intent_taxonomy.json`.
"""

from __future__ import annotations

import re
from typing import Self

from pydantic import BaseModel, Field

from ifap.agents.framework import (
    AgentDependencies,
    AgentDescriptor,
    AgentOutcome,
    BaseAgent,
    agent_plugin,
)
from ifap.application.ports import LLMClient, LLMUnavailableError
from ifap.application.workflow import GenerationRequest, WorkflowState
from ifap.config.settings import WorkflowSettings
from ifap.domain.intent import BusinessIntent, IntentTaxonomy, SurveyTypeProfile

_COUNT_PATTERN = re.compile(r"(\d{1,3})\s*(?:questions?|qs|items)", re.IGNORECASE)
_WORD_PATTERN = re.compile(r"[a-z][a-z\-]{3,}")
_MAX_KEYWORDS = 12

SYSTEM_PROMPT = """You are the Intent Agent of a questionnaire platform.
Classify the user's business need. survey_type MUST be one of: {survey_types}.
Extract objectives (short phrases), keywords, target audience and desired question count."""


class IntentExtraction(BaseModel):
    survey_type: str
    target_audience: str | None = None
    objectives: list[str] = Field(default_factory=list[str])
    keywords: list[str] = Field(default_factory=list[str])
    question_count: int | None = None


@agent_plugin(
    AgentDescriptor(
        name="intent",
        version="1.0.0",
        description="Understands the customer's need and classifies the survey type",
        capabilities=("classification", "extraction"),
    )
)
class IntentAgent(BaseAgent):
    def __init__(
        self, *, llm: LLMClient, taxonomy: IntentTaxonomy, settings: WorkflowSettings
    ) -> None:
        super().__init__(
            max_attempts=settings.agent_max_attempts,
            backoff_seconds=settings.agent_retry_backoff_seconds,
        )
        self._llm = llm
        self._taxonomy = taxonomy
        self._settings = settings

    @classmethod
    def create(cls, deps: AgentDependencies) -> Self:
        return cls(llm=deps.llm, taxonomy=deps.taxonomy, settings=deps.workflow)

    async def _execute(self, state: WorkflowState) -> AgentOutcome:
        heuristic = self._classify_heuristically(state.request)
        try:
            intent = await self._classify_with_llm(state.request, heuristic)
        except LLMUnavailableError as exc:
            return AgentOutcome(
                state=state.model_copy(update={"intent": heuristic}),
                strategy="heuristic",
                note=f"fallback: {exc}",
            )
        return AgentOutcome(state=state.model_copy(update={"intent": intent}), strategy="llm")

    async def _classify_with_llm(
        self, request: GenerationRequest, fallback: BusinessIntent
    ) -> BusinessIntent:
        known = [p.survey_type for p in self._taxonomy.survey_types]
        extraction = await self._llm.generate_structured(
            system=SYSTEM_PROMPT.format(survey_types=", ".join(known)),
            user=request.message,
            output_type=IntentExtraction,
        )
        survey_type = request.survey_type or (
            extraction.survey_type if extraction.survey_type in known else fallback.survey_type
        )
        return self._build_intent(
            request,
            survey_type=survey_type,
            keywords=tuple(extraction.keywords) or fallback.keywords,
            requested_count=request.question_count or extraction.question_count,
            confidence=0.9,
        ).model_copy(
            update={
                "objectives": tuple(extraction.objectives),
                "target_audience": extraction.target_audience,
            }
        )

    def _classify_heuristically(self, request: GenerationRequest) -> BusinessIntent:
        text = request.message.lower()
        scores = {p.survey_type: _keyword_hits(text, p) for p in self._taxonomy.survey_types}
        best_type, best_score = max(scores.items(), key=lambda item: item[1])
        survey_type = request.survey_type or (
            best_type if best_score > 0 else self._taxonomy.default_survey_type
        )
        return self._build_intent(
            request,
            survey_type=survey_type,
            keywords=_extract_keywords(text, self._taxonomy.stopwords),
            requested_count=request.question_count or _extract_count(text),
            confidence=round(best_score / (best_score + 1), 2),
        )

    def _build_intent(
        self,
        request: GenerationRequest,
        *,
        survey_type: str,
        keywords: tuple[str, ...],
        requested_count: int | None,
        confidence: float,
    ) -> BusinessIntent:
        profile = self._taxonomy.profile(survey_type)
        count = min(
            requested_count or self._settings.default_question_count,
            self._settings.max_question_count,
        )
        return BusinessIntent(
            raw_request=request.message,
            survey_type=survey_type,
            business_function=profile.business_function if profile else None,
            industry=profile.industry if profile else None,
            keywords=keywords,
            question_count=max(count, 1),
            confidence=confidence,
        )


def _keyword_hits(text: str, profile: SurveyTypeProfile) -> int:
    return sum(1 for keyword in profile.keywords if keyword in text)


def _extract_count(text: str) -> int | None:
    match = _COUNT_PATTERN.search(text)
    return int(match.group(1)) if match else None


def _extract_keywords(text: str, stopwords: frozenset[str]) -> tuple[str, ...]:
    words = [word for word in _WORD_PATTERN.findall(text) if word not in stopwords]
    return tuple(dict.fromkeys(words))[:_MAX_KEYWORDS]
