"""Application services (use cases). They orchestrate ports and publish domain events;
they contain no I/O details and no framework code.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import batched
from uuid import UUID

from ifap.application.ports import (
    EventPublisher,
    KnowledgeProvider,
    QuestionnaireRepository,
    TemplateSource,
    WorkflowOrchestrator,
)
from ifap.application.workflow import GenerationOutcome, GenerationRequest, WorkflowState
from ifap.domain.errors import AgentExecutionError, NotFoundError
from ifap.domain.events import (
    KnowledgeIngested,
    QuestionnaireGenerated,
    QuestionnairePublished,
    QuestionnaireRevised,
)
from ifap.domain.questionnaire import Question, Questionnaire


class QuestionnaireGenerationService:
    """Implements the `QuestionnaireGenerator` driving port."""

    def __init__(
        self,
        *,
        orchestrator: WorkflowOrchestrator,
        repository: QuestionnaireRepository,
        events: EventPublisher,
    ) -> None:
        self._orchestrator = orchestrator
        self._repository = repository
        self._events = events

    async def generate(self, request: GenerationRequest) -> GenerationOutcome:
        state = await self._orchestrator.run(WorkflowState(request=request))
        outcome = _to_outcome(state)
        await self._repository.save(outcome.questionnaire)
        await self._events.publish(
            QuestionnaireGenerated(
                questionnaire_id=outcome.questionnaire.id,
                survey_type=outcome.questionnaire.survey_type,
                question_count=len(outcome.questionnaire.questions),
                is_valid=outcome.validation.is_valid,
            )
        )
        return outcome


def _to_outcome(state: WorkflowState) -> GenerationOutcome:
    if state.halted:
        raise AgentExecutionError(state.halt_reason or "Workflow halted")
    if state.questionnaire is None or state.intent is None or state.validation is None:
        raise AgentExecutionError("Workflow finished without a validated questionnaire")
    return GenerationOutcome(
        questionnaire=state.questionnaire,
        intent=state.intent,
        validation=state.validation,
        trace=state.trace,
        source_count=len(state.candidates),
    )


class QuestionnaireService:
    def __init__(self, *, repository: QuestionnaireRepository, events: EventPublisher) -> None:
        self._repository = repository
        self._events = events

    async def get(self, questionnaire_id: UUID) -> Questionnaire:
        questionnaire = await self._repository.get(questionnaire_id)
        if questionnaire is None:
            raise NotFoundError(f"Questionnaire {questionnaire_id} not found")
        return questionnaire

    async def list(self, *, limit: int, offset: int) -> list[Questionnaire]:
        return await self._repository.list(limit=limit, offset=offset)

    async def revise(
        self,
        questionnaire_id: UUID,
        *,
        title: str,
        description: str,
        questions: Sequence[Question],
    ) -> Questionnaire:
        current = await self.get(questionnaire_id)
        revised = current.revise(title=title, description=description, questions=tuple(questions))
        await self._repository.save(revised)
        await self._events.publish(
            QuestionnaireRevised(questionnaire_id=revised.id, version=revised.version)
        )
        return revised

    async def publish(self, questionnaire_id: UUID) -> Questionnaire:
        published = (await self.get(questionnaire_id)).publish()
        await self._repository.save(published)
        await self._events.publish(QuestionnairePublished(questionnaire_id=published.id))
        return published


class KnowledgeIngestionService:
    """Ingestion pipeline: load -> validate (by the domain model) -> embed+index in batches."""

    def __init__(
        self, *, knowledge: KnowledgeProvider, events: EventPublisher, batch_size: int
    ) -> None:
        self._knowledge = knowledge
        self._events = events
        self._batch_size = batch_size

    async def ingest(self, source: TemplateSource) -> int:
        templates = source.load()
        indexed = 0
        for batch in batched(templates, self._batch_size, strict=False):
            indexed += await self._knowledge.upsert(batch)
        await self._events.publish(KnowledgeIngested(source=source.name, document_count=indexed))
        return indexed

    async def ensure_seeded(self, source: TemplateSource) -> int:
        if await self._knowledge.count() > 0:
            return 0
        return await self.ingest(source)
