"""Autonomous Questionnaire Builder Agent - the first tool-using, self-correcting agent.

Unlike `questionnaire_builder` (one LLM call), this agent runs a tool loop: the model
searches the knowledge base, submits drafts, reads validation feedback, and revises until
the draft is valid with the requested number of questions - deciding its own next step.

It stays grounded: drafts may only reference template ids the agent has actually retrieved,
so answer types and choices always come from curated templates. Guardrails come from the
runtime (`autonomous_max_tool_calls`, `autonomous_max_seconds`). If the model is unavailable
or never produces a valid draft, the deterministic selector is used instead.

When the workflow routes `validation`'s `invalid`/`incomplete` signal back to this agent,
the second visit receives the previous validation feedback in its goal.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Self

from pydantic import BaseModel, Field, JsonValue

from ifap.agents.builtin.builder_agent import (
    assemble_questionnaire,
    default_title,
    order_by_template,
    select_diverse,
)
from ifap.agents.framework import (
    AgentDependencies,
    AgentDescriptor,
    AgentOutcome,
    BaseAgent,
    agent_plugin,
)
from ifap.agents.runtime import AgentTool, LoopBudget, ToolLoopResult, run_tool_loop
from ifap.application.ports import KnowledgeProvider, LLMClient, LLMUnavailableError
from ifap.application.workflow import WorkflowState
from ifap.config.settings import WorkflowSettings
from ifap.domain.errors import DomainRuleViolationError
from ifap.domain.intent import BusinessIntent, IntentTaxonomy
from ifap.domain.knowledge import KnowledgeQuery, RetrievedTemplate
from ifap.domain.questionnaire import Questionnaire
from ifap.domain.validation import validate_questionnaire

AGENT_NAME = "autonomous_builder"

SYSTEM_PROMPT = """You are the Autonomous Questionnaire Builder Agent.
Goal: a questionnaire with exactly the required number of questions that serves the user's
need, using ONLY question ids from the candidates or from search_templates results.
Work step by step:
1. Review the candidates. Call search_templates if a topic in the need is not covered.
2. Call submit_draft with a title and the chosen question ids.
3. Read the result. If it is not accepted, fix the problems and submit again.
   A question that depends on another needs that parent question included before it.
4. When a draft is accepted, reply with one sentence summarising it and stop."""


class SearchTemplatesArgs(BaseModel):
    query: str = Field(min_length=2, description="What to look for, e.g. 'manager feedback'")
    top_k: int = Field(default=10, ge=1, le=25)


class SubmitDraftArgs(BaseModel):
    title: str = Field(min_length=3)
    question_ids: list[str] = Field(min_length=1, description="Ids in the desired order")


class _BuildSession:
    """Per-run working memory: the candidate pool and the best accepted draft so far."""

    def __init__(
        self,
        intent: BusinessIntent,
        candidates: Sequence[RetrievedTemplate],
        knowledge: KnowledgeProvider,
    ) -> None:
        self._intent = intent
        self._knowledge = knowledge
        self.pool = {c.template.question.id: c for c in candidates}
        self.best: Questionnaire | None = None

    async def search(self, raw: Mapping[str, JsonValue]) -> str:
        args = SearchTemplatesArgs.model_validate(raw)
        query = KnowledgeQuery(
            text=args.query, top_k=args.top_k, survey_type=self._intent.survey_type
        )
        results = await self._knowledge.search(query)
        for result in results:
            self.pool.setdefault(result.template.question.id, result)
        return json.dumps([_brief(result) for result in results])

    async def submit(self, raw: Mapping[str, JsonValue]) -> str:
        args = SubmitDraftArgs.model_validate(raw)
        ids = list(dict.fromkeys(args.question_ids))
        unknown = [qid for qid in ids if qid not in self.pool]
        if unknown:
            raise DomainRuleViolationError(f"Unknown question ids (search first): {unknown}")
        chosen = order_by_template([self.pool[qid] for qid in ids])
        questions = tuple(c.template.question for c in chosen)
        draft = assemble_questionnaire(
            self._intent, questions, chosen, args.title, self._intent.raw_request
        )
        report = validate_questionnaire(draft)
        accepted = report.is_valid and len(questions) == self._intent.question_count
        if accepted:
            self.best = draft
        return json.dumps(
            {
                "accepted": accepted,
                "question_count": len(questions),
                "required_count": self._intent.question_count,
                "issues": [issue.message for issue in report.issues],
            }
        )


@agent_plugin(
    AgentDescriptor(
        name=AGENT_NAME,
        version="1.0.0",
        description="Tool-using builder that searches, drafts, validates and self-corrects",
        capabilities=("generation", "tool_use", "self_correction"),
    )
)
class AutonomousBuilderAgent(BaseAgent):
    def __init__(
        self,
        *,
        llm: LLMClient,
        knowledge: KnowledgeProvider,
        taxonomy: IntentTaxonomy,
        settings: WorkflowSettings,
    ) -> None:
        super().__init__(
            max_attempts=settings.agent_max_attempts,
            backoff_seconds=settings.agent_retry_backoff_seconds,
        )
        self._llm = llm
        self._knowledge = knowledge
        self._taxonomy = taxonomy
        self._catalogue_size = settings.builder_llm_max_candidates
        self._budget = LoopBudget(
            max_tool_calls=settings.autonomous_max_tool_calls,
            max_seconds=settings.autonomous_max_seconds,
        )

    @classmethod
    def create(cls, deps: AgentDependencies) -> Self:
        return cls(
            llm=deps.llm, knowledge=deps.knowledge, taxonomy=deps.taxonomy, settings=deps.workflow
        )

    async def _execute(self, state: WorkflowState) -> AgentOutcome:
        intent = state.intent
        if intent is None or not state.candidates:
            raise DomainRuleViolationError("Autonomous builder requires an intent and candidates")
        session = _BuildSession(intent, state.candidates, self._knowledge)
        try:
            loop = await self._run_loop(state, intent, session)
        except LLMUnavailableError as exc:
            return self._fallback(state, intent, f"fallback: {exc}")
        if session.best is None:
            return self._fallback(
                state.with_artifact(AGENT_NAME, loop),
                intent,
                f"fallback: no accepted draft (stop={loop.stop_reason})",
            )
        return AgentOutcome(
            state=state.model_copy(update={"questionnaire": session.best}).with_artifact(
                AGENT_NAME, loop
            ),
            strategy="autonomous",
            note=f"{len(session.best.questions)} questions; {len(loop.steps)} tool calls; "
            f"{loop.model_turns} model turns; stop={loop.stop_reason}",
        )

    async def _run_loop(
        self, state: WorkflowState, intent: BusinessIntent, session: _BuildSession
    ) -> ToolLoopResult:
        tools = [
            AgentTool.from_model(
                name="search_templates",
                description="Search the template knowledge base for more candidate questions",
                args=SearchTemplatesArgs,
                handler=session.search,
            ),
            AgentTool.from_model(
                name="submit_draft",
                description="Submit a draft questionnaire; returns validation feedback",
                args=SubmitDraftArgs,
                handler=session.submit,
            ),
        ]
        return await run_tool_loop(
            self._llm,
            system=SYSTEM_PROMPT,
            goal=self._goal(state, intent),
            tools=tools,
            budget=self._budget,
        )

    def _goal(self, state: WorkflowState, intent: BusinessIntent) -> str:
        catalogue = "\n".join(
            f"{c.template.question.id} | {c.template.question.category} | "
            f"{c.template.question.answer_type} | {c.template.question.label}"
            for c in state.candidates[: self._catalogue_size]
        )
        lines = [
            f"Need: {intent.raw_request}",
            f"Required question count: {intent.question_count}",
            f"Suggested title: {default_title(intent, self._taxonomy)}",
            _feedback(state),
            f"Candidates (id | category | type | label):\n{catalogue}",
        ]
        return "\n".join(line for line in lines if line)

    def _fallback(self, state: WorkflowState, intent: BusinessIntent, note: str) -> AgentOutcome:
        chosen = order_by_template(select_diverse(state.candidates, intent.question_count))
        questionnaire = assemble_questionnaire(
            intent,
            tuple(c.template.question for c in chosen),
            chosen,
            default_title(intent, self._taxonomy),
            intent.raw_request,
        )
        return AgentOutcome(
            state=state.model_copy(update={"questionnaire": questionnaire}),
            strategy="heuristic",
            note=f"{len(questionnaire.questions)} questions; {note}",
        )


def _feedback(state: WorkflowState) -> str:
    """On a retry visit, tell the model what the Validation Agent found last time."""
    if state.validation is None or state.questionnaire is None:
        return ""
    issues = "; ".join(issue.message for issue in state.validation.issues) or "none"
    return (
        f"Previous attempt had {len(state.questionnaire.questions)} questions and was sent "
        f"back by validation. Issues: {issues}"
    )


def _brief(result: RetrievedTemplate) -> dict[str, JsonValue]:
    question = result.template.question
    return {
        "id": question.id,
        "category": question.category,
        "type": question.answer_type.value,
        "label": question.label,
        "score": round(result.score, 3),
    }
