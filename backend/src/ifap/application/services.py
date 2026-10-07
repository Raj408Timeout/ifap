"""Application services (use cases). They orchestrate ports and publish domain events;
they contain no I/O details and no framework code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import batched
from uuid import UUID

from ifap.application.ports import (
    EventPublisher,
    KnowledgeProvider,
    QuestionnaireRepository,
    StepObserver,
    TemplateSource,
    WorkflowOrchestrator,
)
from ifap.application.workflow import GenerationOutcome, GenerationRequest, WorkflowState
from ifap.domain.errors import AgentExecutionError, DomainRuleViolationError, NotFoundError
from ifap.domain.events import (
    KnowledgeIngested,
    QuestionnaireGenerated,
    QuestionnairePublished,
    QuestionnaireRevised,
)
from ifap.domain.questionnaire import Question, Questionnaire
from ifap.domain.validation import ValidationReport, validate_questionnaire


class QuestionnaireGenerationService:
    """Implements the `QuestionnaireGenerator` driving port over one or more named workflows."""

    def __init__(
        self,
        *,
        orchestrators: Mapping[str, WorkflowOrchestrator],
        default_workflow: str,
        repository: QuestionnaireRepository,
        events: EventPublisher,
    ) -> None:
        self._orchestrators = dict(orchestrators)
        self._default_workflow = default_workflow
        self._repository = repository
        self._events = events

    @property
    def workflows(self) -> dict[str, list[str]]:
        return {name: o.pipeline for name, o in self._orchestrators.items()}

    @property
    def default_workflow(self) -> str:
        return self._default_workflow

    async def generate(
        self, request: GenerationRequest, on_step: StepObserver | None = None
    ) -> GenerationOutcome:
        orchestrator = self._orchestrators[self.resolve_workflow(request.workflow)]
        state = await orchestrator.run(WorkflowState(request=request), on_step)
        outcome = _to_outcome(state, orchestrator.name)
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

    def resolve_workflow(self, workflow: str | None) -> str:
        """The workflow a request will run; raises for unknown names (fail fast)."""
        name = workflow or self._default_workflow
        if name not in self._orchestrators:
            known = ", ".join(sorted(self._orchestrators))
            raise DomainRuleViolationError(f"Unknown workflow '{name}' (known: {known})")
        return name


def _to_outcome(state: WorkflowState, workflow: str) -> GenerationOutcome:
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
        workflow=workflow,
        artifacts=state.artifacts,
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

    async def validate(self, questionnaire_id: UUID) -> ValidationReport:
        return validate_questionnaire(await self.get(questionnaire_id))

    async def publish(self, questionnaire_id: UUID) -> Questionnaire:
        published = (await self.get(questionnaire_id)).publish()
        await self._repository.save(published)
        await self._events.publish(QuestionnairePublished(questionnaire_id=published.id))
        return published


class QuestionnaireAssemblyService:
    """Builds a questionnaire from template ids chosen by the caller - e.g. Claude acting as
    the builder through MCP. Grounded by construction: only ids that exist in the knowledge
    base are accepted, so answer types and choices always come from curated templates."""

    def __init__(
        self,
        *,
        knowledge: KnowledgeProvider,
        repository: QuestionnaireRepository,
        events: EventPublisher,
    ) -> None:
        self._knowledge = knowledge
        self._repository = repository
        self._events = events

    async def assemble(
        self, *, title: str, survey_type: str, template_ids: Sequence[str], description: str = ""
    ) -> tuple[Questionnaire, ValidationReport]:
        unique = list(dict.fromkeys(template_ids))
        if not unique:
            raise DomainRuleViolationError("Select at least one template id")
        templates = await self._knowledge.get(unique)
        missing = sorted(set(unique) - {t.template_id for t in templates})
        if missing:
            raise DomainRuleViolationError(f"Unknown template ids: {', '.join(missing)}")
        questionnaire = Questionnaire(
            title=title,
            description=description,
            survey_type=survey_type,
            questions=tuple(t.question for t in templates),
            source_template_ids=tuple(unique),
        )
        report = validate_questionnaire(questionnaire)
        await self._repository.save(questionnaire)
        await self._events.publish(
            QuestionnaireGenerated(
                questionnaire_id=questionnaire.id,
                survey_type=survey_type,
                question_count=len(questionnaire.questions),
                is_valid=report.is_valid,
            )
        )
        return questionnaire, report


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
