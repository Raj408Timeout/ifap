"""Template Retrieval Agent - RAG step that fetches candidate questions from the knowledge base.

It only talks to the `KnowledgeProvider` port, so the vector store is swappable.
If the filtered search yields too few candidates, the search is broadened (no type filter).
"""

from __future__ import annotations

from typing import Self

from ifap.agents.framework import (
    AgentDependencies,
    AgentDescriptor,
    AgentOutcome,
    BaseAgent,
    agent_plugin,
)
from ifap.application.ports import KnowledgeProvider
from ifap.application.workflow import WorkflowState
from ifap.config.settings import WorkflowSettings
from ifap.domain.errors import DomainRuleViolationError
from ifap.domain.intent import BusinessIntent
from ifap.domain.knowledge import KnowledgeQuery, RetrievedTemplate


@agent_plugin(
    AgentDescriptor(
        name="template_retrieval",
        version="1.0.0",
        description="Retrieves relevant questionnaire templates via RAG",
        capabilities=("retrieval", "rag"),
    )
)
class TemplateRetrievalAgent(BaseAgent):
    def __init__(self, *, knowledge: KnowledgeProvider, settings: WorkflowSettings) -> None:
        super().__init__(
            max_attempts=settings.agent_max_attempts,
            backoff_seconds=settings.agent_retry_backoff_seconds,
        )
        self._knowledge = knowledge
        self._settings = settings

    @classmethod
    def create(cls, deps: AgentDependencies) -> Self:
        return cls(knowledge=deps.knowledge, settings=deps.workflow)

    async def _execute(self, state: WorkflowState) -> AgentOutcome:
        if state.intent is None:
            raise DomainRuleViolationError("Template retrieval requires an intent")
        candidates = await self._search(state.intent, filtered=True)
        strategy = "filtered"
        if len(candidates) < state.intent.question_count:
            candidates = await self._search(state.intent, filtered=False)
            strategy = "broadened"
        return AgentOutcome(
            state=state.model_copy(update={"candidates": tuple(candidates)}),
            strategy=strategy,
            note=f"{len(candidates)} candidates",
        )

    async def _search(self, intent: BusinessIntent, *, filtered: bool) -> list[RetrievedTemplate]:
        query = KnowledgeQuery(
            text=" ".join((intent.raw_request, *intent.objectives, *intent.keywords)),
            top_k=max(self._settings.retrieval_top_k, intent.question_count * 2),
            survey_type=intent.survey_type if filtered else None,
        )
        results = await self._knowledge.search(query)
        return [r for r in results if r.score >= self._settings.retrieval_min_score]
