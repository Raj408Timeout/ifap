"""Validation Agent - checks the generated questionnaire and auto-repairs safe issues.

Repairs are conservative and deterministic: dangling skip-logic rules are removed and
duplicate questions dropped. Anything else is reported back for the human reviewer.
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
from ifap.application.workflow import WorkflowState
from ifap.config.settings import WorkflowSettings
from ifap.domain.errors import DomainRuleViolationError
from ifap.domain.questionnaire import Question, Questionnaire
from ifap.domain.validation import validate_questionnaire


@agent_plugin(
    AgentDescriptor(
        name="validation",
        version="1.0.0",
        description="Validates questionnaire structure, skip logic and duplicates",
        capabilities=("validation", "repair"),
    )
)
class ValidationAgent(BaseAgent):
    def __init__(self, *, settings: WorkflowSettings) -> None:
        super().__init__(
            max_attempts=settings.agent_max_attempts,
            backoff_seconds=settings.agent_retry_backoff_seconds,
        )

    @classmethod
    def create(cls, deps: AgentDependencies) -> Self:
        return cls(settings=deps.workflow)

    async def _execute(self, state: WorkflowState) -> AgentOutcome:
        if state.questionnaire is None:
            raise DomainRuleViolationError("Nothing to validate")
        repaired, repairs = repair(state.questionnaire)
        report = validate_questionnaire(repaired)
        return AgentOutcome(
            state=state.model_copy(update={"questionnaire": repaired, "validation": report}),
            note=f"{repairs} repairs, {len(report.issues)} issues",
        )


def repair(questionnaire: Questionnaire) -> tuple[Questionnaire, int]:
    seen_ids: set[str] = set()
    seen_labels: set[str] = set()
    kept: list[Question] = []
    repairs = 0
    for question in questionnaire.questions:
        label_key = question.label.strip().lower()
        if question.id in seen_ids or label_key in seen_labels:
            repairs += 1
            continue
        cleaned = _drop_dangling_rules(question, seen_ids)
        repairs += int(cleaned is not question)
        kept.append(cleaned)
        seen_ids.add(question.id)
        seen_labels.add(label_key)
    return questionnaire.model_copy(update={"questions": tuple(kept)}), repairs


def _drop_dangling_rules(question: Question, earlier_ids: set[str]) -> Question:
    valid = tuple(rule for rule in question.dependency_rules if rule.depends_on in earlier_ids)
    if len(valid) == len(question.dependency_rules):
        return question
    return question.model_copy(update={"dependency_rules": valid})
