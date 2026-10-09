"""IFAP as an MCP server.

Hexagonal view: this is a second *driving adapter* next to the REST API. Both call the same
application services through the composition root, so MCP adds no business logic and the
core is unchanged. Any MCP host (Claude Desktop, Claude Code, an IDE, another agent) can:

* explore the knowledge base           - `list_survey_types`, `search_templates`
* run IFAP's own agent workflows       - `generate_questionnaire` (standard | autonomous),
                                         `get_generation_result` (poll long runs)
* act as the builder itself            - `build_questionnaire_from_templates`
* review and manage results            - `get_questionnaire`, `list_questionnaires`,
                                         `publish_questionnaire`
* inspect and control the platform     - `get_platform_status`, `set_llm_enabled`

Domain and validation errors are re-raised as `ToolError`, so the calling model sees a clear
message it can act on rather than an opaque crash.
"""

from __future__ import annotations

from collections.abc import Awaitable
from uuid import UUID

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from ifap.agents.framework import AgentDescriptor
from ifap.api.container import Container
from ifap.application.jobs import JobSnapshot, JobStatus
from ifap.application.ports import LLMStatus
from ifap.application.workflow import AgentTrace, GenerationOutcome, GenerationRequest
from ifap.domain.errors import IFAPError
from ifap.domain.knowledge import KnowledgeQuery, RetrievedTemplate
from ifap.domain.questionnaire import Question, Questionnaire
from ifap.domain.validation import ValidationReport

INSTRUCTIONS = """IFAP generates validated survey questionnaires from a curated knowledge base
of question templates. Two ways to build one:
1. generate_questionnaire - IFAP's agents classify the need, retrieve templates, build and
   validate. Use workflow="autonomous" for the self-correcting, tool-using builder agent.
   Runs on a local LLM can take minutes: if the result has status "running", keep calling
   get_generation_result with its job_id until the status is "succeeded" or "failed".
2. Act as the builder yourself: list_survey_types -> search_templates (several queries) ->
   build_questionnaire_from_templates with the ids you chose -> fix any validation issues.
Questions with depends_on need their parent question included earlier."""


# ---------------------------------------------------------------- tool result models


class SurveyTypeInfo(BaseModel):
    survey_type: str
    title: str
    business_function: str
    industry: str


class TemplateHit(BaseModel):
    id: str
    template: str
    category: str
    answer_type: str
    label: str
    choices: list[str]
    depends_on: list[str]
    score: float


class QuestionView(BaseModel):
    id: str
    label: str
    category: str
    answer_type: str
    choices: list[str]
    depends_on: list[str]


class QuestionnaireView(BaseModel):
    id: str
    title: str
    survey_type: str
    status: str
    version: int
    question_count: int
    is_valid: bool
    issues: list[str]
    questions: list[QuestionView]


class GenerationResult(BaseModel):
    workflow: str
    interpreted_survey_type: str
    interpreted_question_count: int
    candidates_considered: int
    questionnaire: QuestionnaireView
    trace: list[AgentTrace]


class GenerationJobView(BaseModel):
    job_id: str
    status: str
    elapsed_seconds: float
    completed_steps: list[str]
    result: GenerationResult | None
    error: str | None
    next_action: str


class QuestionnaireListItem(BaseModel):
    id: str
    title: str
    survey_type: str
    status: str
    version: int
    question_count: int


class PlatformStatus(BaseModel):
    default_workflow: str
    workflows: dict[str, list[str]]
    llm: LLMStatus
    knowledge_documents: int
    agents: list[AgentDescriptor]


# ---------------------------------------------------------------- mapping helpers


def _question_view(question: Question) -> QuestionView:
    return QuestionView(
        id=question.id,
        label=question.label,
        category=question.category,
        answer_type=question.answer_type.value,
        choices=[choice.label for choice in question.choices],
        depends_on=[rule.depends_on for rule in question.dependency_rules],
    )


def _questionnaire_view(
    questionnaire: Questionnaire, report: ValidationReport
) -> QuestionnaireView:
    return QuestionnaireView(
        id=str(questionnaire.id),
        title=questionnaire.title,
        survey_type=questionnaire.survey_type,
        status=questionnaire.status.value,
        version=questionnaire.version,
        question_count=len(questionnaire.questions),
        is_valid=report.is_valid,
        issues=[f"[{issue.severity}] {issue.message}" for issue in report.issues],
        questions=[_question_view(question) for question in questionnaire.questions],
    )


def _template_hit(result: RetrievedTemplate) -> TemplateHit:
    view = _question_view(result.template.question)
    return TemplateHit(
        id=view.id,
        template=result.template.template_name,
        category=view.category,
        answer_type=view.answer_type,
        label=view.label,
        choices=view.choices,
        depends_on=view.depends_on,
        score=round(result.score, 3),
    )


def _generation_result(outcome: GenerationOutcome) -> GenerationResult:
    return GenerationResult(
        workflow=outcome.workflow,
        interpreted_survey_type=outcome.intent.survey_type,
        interpreted_question_count=outcome.intent.question_count,
        candidates_considered=outcome.source_count,
        questionnaire=_questionnaire_view(outcome.questionnaire, outcome.validation),
        trace=list(outcome.trace),
    )


_NEXT_ACTION = {
    JobStatus.RUNNING: "Still running. Call get_generation_result with job_id='{job_id}'.",
    JobStatus.SUCCEEDED: "Done - the questionnaire is saved as a draft.",
    JobStatus.FAILED: "Generation failed - see error.",
}


def _step_line(step: AgentTrace) -> str:
    """e.g. 'intent: llm via gemini-3.6-flash (1.4 s)' - always names the model that answered."""
    via = f" via {', '.join(step.models)}" if step.models else ""
    return f"{step.agent}: {step.strategy or step.status}{via} ({step.duration_ms / 1000:.1f} s)"


def _job_view(snapshot: JobSnapshot) -> GenerationJobView:
    return GenerationJobView(
        job_id=snapshot.job_id,
        status=snapshot.status.value,
        elapsed_seconds=snapshot.elapsed_seconds,
        completed_steps=[_step_line(step) for step in snapshot.completed_steps],
        result=_generation_result(snapshot.outcome) if snapshot.outcome else None,
        error=snapshot.error,
        next_action=_NEXT_ACTION[snapshot.status].format(job_id=snapshot.job_id),
    )


def _parse_id(questionnaire_id: str) -> UUID:
    try:
        return UUID(questionnaire_id)
    except ValueError as exc:
        raise ToolError(f"'{questionnaire_id}' is not a valid questionnaire id") from exc


async def _guard[T](action: Awaitable[T]) -> T:
    """Translate domain and input errors into messages the calling model can act on."""
    try:
        return await action
    except (IFAPError, ValidationError) as exc:
        raise ToolError(str(exc)) from exc


# ---------------------------------------------------------------- server


class IFAPTools:
    """Tool implementations, bound to one container (dependency injection, no globals)."""

    def __init__(self, container: Container) -> None:
        self._c = container

    async def get_platform_status(self) -> PlatformStatus:
        """Workflows, registered agents, LLM provider/switch state and knowledge-base size."""
        return PlatformStatus(
            default_workflow=self._c.generator.default_workflow,
            workflows=self._c.generator.workflows,
            llm=self._c.llm.status(),
            knowledge_documents=await self._c.knowledge.count(),
            agents=list(self._c.agent_descriptors),
        )

    async def list_survey_types(self) -> list[SurveyTypeInfo]:
        """Survey types the knowledge base covers. Use them as `survey_type` filters."""
        return [
            SurveyTypeInfo(
                survey_type=p.survey_type,
                title=p.title,
                business_function=p.business_function,
                industry=p.industry,
            )
            for p in self._c.taxonomy.survey_types
        ]

    async def search_templates(
        self, query: str, survey_type: str | None = None, top_k: int = 10
    ) -> list[TemplateHit]:
        """Semantic search over question templates (RAG). Returns ids usable in
        build_questionnaire_from_templates, with answer type, choices and skip-logic parents."""
        knowledge_query = await _guard(_validated_query(query, survey_type, top_k))
        results = await self._c.knowledge.search(knowledge_query)
        return [_template_hit(result) for result in results]

    async def generate_questionnaire(
        self,
        request: str,
        question_count: int | None = None,
        survey_type: str | None = None,
        workflow: str | None = None,
    ) -> GenerationJobView:
        """Run IFAP's agent workflow on a natural-language need and save the draft.
        workflow: "standard" (fixed pipeline) or "autonomous" (tool-using, self-correcting
        builder). Waits up to ~40 s; if status is "running", poll get_generation_result."""
        generation = await _guard(
            _validated_request(request, question_count, survey_type, workflow)
        )
        await _guard(_resolve_workflow(self._c, generation.workflow))
        job_id = self._c.jobs.start(generation)
        return _job_view(await self._c.jobs.wait(job_id, self._c.settings.mcp.wait_seconds))

    async def get_generation_result(
        self, job_id: str, wait_seconds: float | None = None
    ) -> GenerationJobView:
        """Status of a generate_questionnaire job. Waits up to ~40 s for it to finish and
        reports the agent steps completed so far while it is still running."""
        limit = self._c.settings.mcp.wait_seconds
        wait = limit if wait_seconds is None else max(0.0, min(wait_seconds, limit))
        return _job_view(await _guard(self._c.jobs.wait(job_id, wait)))

    async def build_questionnaire_from_templates(
        self, title: str, survey_type: str, template_ids: list[str], description: str = ""
    ) -> QuestionnaireView:
        """Create and save a draft from template ids YOU chose (order is kept). Returns
        validation issues - fix them and call again; each call creates a new draft."""
        questionnaire, report = await _guard(
            self._c.assembly.assemble(
                title=title,
                survey_type=survey_type,
                template_ids=template_ids,
                description=description,
            )
        )
        return _questionnaire_view(questionnaire, report)

    async def get_questionnaire(self, questionnaire_id: str) -> QuestionnaireView:
        """Full questionnaire with its current validation result."""
        qid = _parse_id(questionnaire_id)
        questionnaire = await _guard(self._c.questionnaires.get(qid))
        report = await _guard(self._c.questionnaires.validate(qid))
        return _questionnaire_view(questionnaire, report)

    async def list_questionnaires(self, limit: int = 10) -> list[QuestionnaireListItem]:
        """Most recently updated questionnaires (any source: UI, API or MCP)."""
        items = await self._c.questionnaires.list(limit=max(1, min(limit, 50)), offset=0)
        return [
            QuestionnaireListItem(
                id=str(q.id),
                title=q.title,
                survey_type=q.survey_type,
                status=q.status.value,
                version=q.version,
                question_count=len(q.questions),
            )
            for q in items
        ]

    async def publish_questionnaire(self, questionnaire_id: str) -> QuestionnaireView:
        """Publish (lock) a draft. Published questionnaires can no longer be edited."""
        qid = _parse_id(questionnaire_id)
        published = await _guard(self._c.questionnaires.publish(qid))
        return _questionnaire_view(published, await _guard(self._c.questionnaires.validate(qid)))

    async def set_llm_enabled(self, enabled: bool) -> LLMStatus:
        """Turn IFAP's own LLM-backed agent strategies on or off (off = deterministic
        heuristics, fast). Affects this MCP server process only."""
        return await _guard(_set_llm(self._c, enabled))


async def _validated_request(
    message: str, question_count: int | None, survey_type: str | None, workflow: str | None
) -> GenerationRequest:
    return GenerationRequest(
        message=message, question_count=question_count, survey_type=survey_type, workflow=workflow
    )


async def _resolve_workflow(container: Container, workflow: str | None) -> str:
    return container.generator.resolve_workflow(workflow)


async def _validated_query(query: str, survey_type: str | None, top_k: int) -> KnowledgeQuery:
    return KnowledgeQuery(text=query, survey_type=survey_type, top_k=top_k)


async def _set_llm(container: Container, enabled: bool) -> LLMStatus:
    return container.llm.set_enabled(enabled)


def build_mcp_server(container: Container) -> MCPServer:
    tools = IFAPTools(container)
    server = MCPServer(
        name="ifap",
        title="Intelligent Feedback & Assessment Platform",
        instructions=INSTRUCTIONS,
        version=container.settings.api.version,
    )
    for handler in (
        tools.get_platform_status,
        tools.list_survey_types,
        tools.search_templates,
        tools.generate_questionnaire,
        tools.get_generation_result,
        tools.build_questionnaire_from_templates,
        tools.get_questionnaire,
        tools.list_questionnaires,
        tools.publish_questionnaire,
        tools.set_llm_enabled,
    ):
        server.add_tool(handler)
    return server
