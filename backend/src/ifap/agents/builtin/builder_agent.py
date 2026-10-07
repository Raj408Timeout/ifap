"""Questionnaire Builder Agent - assembles a questionnaire from retrieved templates.

Grounded generation: the LLM may only *select and rephrase* retrieved questions (by id); it
cannot invent answer schemas. Without an LLM, a deterministic selector picks the highest-scoring
questions while keeping category diversity and pulling in skip-logic parent questions.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
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
from ifap.application.workflow import WorkflowState
from ifap.config.settings import WorkflowSettings
from ifap.domain.errors import DomainRuleViolationError
from ifap.domain.intent import BusinessIntent, IntentTaxonomy
from ifap.domain.knowledge import RetrievedTemplate
from ifap.domain.questionnaire import Question, Questionnaire

SYSTEM_PROMPT = """You are the Questionnaire Builder Agent.
Select exactly {count} questions from the candidate list that best serve the user's need.
Prefer coverage across categories. You may rephrase a label to fit the context, but only
reference candidate ids. Keep skip-logic parents when you select a dependent question."""

USER_PROMPT = """Need: {need}

Candidates (id | category | type | label):
{catalogue}"""


class LabelRewrite(BaseModel):
    id: str
    label: str


class BuilderPlan(BaseModel):
    title: str
    description: str
    selected_ids: list[str]
    rewrites: list[LabelRewrite] = Field(default_factory=list[LabelRewrite])


@agent_plugin(
    AgentDescriptor(
        name="questionnaire_builder",
        version="1.0.0",
        description="Builds a questionnaire from retrieved templates",
        capabilities=("generation",),
    )
)
class QuestionnaireBuilderAgent(BaseAgent):
    def __init__(
        self, *, llm: LLMClient, taxonomy: IntentTaxonomy, settings: WorkflowSettings
    ) -> None:
        super().__init__(
            max_attempts=settings.agent_max_attempts,
            backoff_seconds=settings.agent_retry_backoff_seconds,
        )
        self._llm = llm
        self._taxonomy = taxonomy
        self._max_llm_candidates = settings.builder_llm_max_candidates

    @classmethod
    def create(cls, deps: AgentDependencies) -> Self:
        return cls(llm=deps.llm, taxonomy=deps.taxonomy, settings=deps.workflow)

    async def _execute(self, state: WorkflowState) -> AgentOutcome:
        if state.intent is None:
            raise DomainRuleViolationError("Builder requires an intent")
        if not state.candidates:
            raise DomainRuleViolationError("No templates matched the request")
        fallback = ""
        try:
            questionnaire = await self._build_with_llm(state.intent, state.candidates)
            strategy = "llm"
        except LLMUnavailableError as exc:
            questionnaire = self._build_heuristically(state.intent, state.candidates)
            strategy = "heuristic"
            fallback = f"; fallback: {exc}"
        return AgentOutcome(
            state=state.model_copy(update={"questionnaire": questionnaire}),
            strategy=strategy,
            note=f"{len(questionnaire.questions)} questions{fallback}",
        )

    async def _build_with_llm(
        self, intent: BusinessIntent, candidates: Sequence[RetrievedTemplate]
    ) -> Questionnaire:
        # Candidates arrive ranked by relevance; the LLM only sees the strongest ones.
        candidates = candidates[: max(self._max_llm_candidates, intent.question_count)]
        catalogue = "\n".join(
            f"{c.template.question.id} | {c.template.question.category} | "
            f"{c.template.question.answer_type} | {c.template.question.label}"
            for c in candidates
        )
        plan = await self._llm.generate_structured(
            system=SYSTEM_PROMPT.format(count=intent.question_count),
            user=USER_PROMPT.format(need=intent.raw_request, catalogue=catalogue),
            output_type=BuilderPlan,
        )
        by_id = {c.template.question.id: c for c in candidates}
        chosen = [by_id[qid] for qid in dict.fromkeys(plan.selected_ids) if qid in by_id]
        if len(chosen) < max(1, intent.question_count // 2):
            raise LLMUnavailableError("LLM plan was not grounded in the candidates")
        rewrites = {r.id: r.label for r in plan.rewrites if r.id in by_id}
        questions = tuple(
            _apply_rewrite(c.template.question, rewrites) for c in order_by_template(chosen)
        )
        return self._questionnaire(intent, questions, chosen, plan.title, plan.description)

    def _build_heuristically(
        self, intent: BusinessIntent, candidates: Sequence[RetrievedTemplate]
    ) -> Questionnaire:
        chosen = select_diverse(candidates, intent.question_count)
        title = default_title(intent, self._taxonomy)
        questions = tuple(c.template.question for c in order_by_template(chosen))
        return self._questionnaire(intent, questions, chosen, title, intent.raw_request)

    def _questionnaire(
        self,
        intent: BusinessIntent,
        questions: tuple[Question, ...],
        chosen: Sequence[RetrievedTemplate],
        title: str,
        description: str,
    ) -> Questionnaire:
        return assemble_questionnaire(intent, questions, chosen, title, description)


def default_title(intent: BusinessIntent, taxonomy: IntentTaxonomy) -> str:
    profile = taxonomy.profile(intent.survey_type)
    return profile.title if profile else intent.survey_type.replace("_", " ").title()


def assemble_questionnaire(
    intent: BusinessIntent,
    questions: tuple[Question, ...],
    chosen: Sequence[RetrievedTemplate],
    title: str,
    description: str,
) -> Questionnaire:
    return Questionnaire(
        title=title,
        description=description,
        survey_type=intent.survey_type,
        questions=questions,
        source_template_ids=tuple(c.template.template_id for c in chosen),
    )


def select_diverse(candidates: Sequence[RetrievedTemplate], count: int) -> list[RetrievedTemplate]:
    """Round-robin over categories (ranked by best score); parents precede dependent questions."""
    by_id = {c.template.question.id: c for c in candidates}
    selected: dict[str, RetrievedTemplate] = {}
    for candidate in _round_robin_by_category(candidates):
        if len(selected) >= count:
            break
        _select_with_parents(candidate, by_id, selected, count)
    return list(selected.values())


def _round_robin_by_category(candidates: Sequence[RetrievedTemplate]) -> list[RetrievedTemplate]:
    buckets: dict[str, list[RetrievedTemplate]] = defaultdict(list)
    for candidate in sorted(candidates, key=lambda c: c.score, reverse=True):
        buckets[candidate.template.question.category].append(candidate)
    ordered: list[RetrievedTemplate] = []
    queues = list(buckets.values())
    while any(queues):
        ordered.extend(queue.pop(0) for queue in queues if queue)
    return ordered


def _select_with_parents(
    candidate: RetrievedTemplate,
    by_id: dict[str, RetrievedTemplate],
    selected: dict[str, RetrievedTemplate],
    count: int,
) -> None:
    question = candidate.template.question
    parents = [
        by_id[rule.depends_on]
        for rule in question.dependency_rules
        if rule.depends_on in by_id and rule.depends_on not in selected
    ]
    missing_parent = any(rule.depends_on not in by_id for rule in question.dependency_rules)
    if missing_parent or len(selected) + len(parents) + 1 > count:
        return
    for parent in parents:
        selected[parent.template.question.id] = parent
    selected[question.id] = candidate


def order_by_template(chosen: Sequence[RetrievedTemplate]) -> list[RetrievedTemplate]:
    """Template order keeps skip-logic parents before their dependants."""
    return sorted(chosen, key=lambda c: c.template.template_id)


def _apply_rewrite(question: Question, rewrites: dict[str, str]) -> Question:
    label = rewrites.get(question.id)
    return question.model_copy(update={"label": label}) if label else question
