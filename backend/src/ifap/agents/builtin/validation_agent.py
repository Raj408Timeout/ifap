"""Validation Agent - checks the generated questionnaire and auto-repairs safe issues.

Repairs are conservative and deterministic (see `domain.validation.repair_questionnaire`).
The agent also emits a routing *signal* - it never names the next agent itself:

* `invalid`    - errors remain after repair
* `incomplete` - fewer questions than the intent asked for

The workflow definition decides what a signal means (e.g. "go back to the builder"); in the
standard workflow no route is configured, so the run simply ends.
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
from ifap.domain.validation import repair_questionnaire, validate_questionnaire

SIGNAL_INVALID = "invalid"
SIGNAL_INCOMPLETE = "incomplete"


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
        repaired, repairs = repair_questionnaire(state.questionnaire)
        report = validate_questionnaire(repaired)
        signal = _signal(report.is_valid, len(repaired.questions), state)
        update: dict[str, object] = {"questionnaire": repaired, "validation": report}
        return AgentOutcome(
            state=state.model_copy(update=update).with_signal(signal),
            note=f"{repairs} repairs, {len(report.issues)} issues"
            + (f", signal={signal}" if signal else ""),
        )


def _signal(is_valid: bool, question_count: int, state: WorkflowState) -> str | None:
    if not is_valid:
        return SIGNAL_INVALID
    if state.intent is not None and question_count < state.intent.question_count:
        return SIGNAL_INCOMPLETE
    return None
