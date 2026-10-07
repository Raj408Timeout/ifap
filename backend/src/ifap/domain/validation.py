"""Pure, deterministic questionnaire validation rules (used by the Validation Agent).

Each rule is a small function `(Questionnaire) -> list[ValidationIssue]`, so new rules
are added by appending to `DEFAULT_RULES` without touching existing ones (OCP).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ifap.domain.questionnaire import Question, Questionnaire


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    severity: Severity = Severity.ERROR
    question_id: str | None = None


class ValidationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return all(issue.severity is not Severity.ERROR for issue in self.issues)


ValidationRule = Callable[[Questionnaire], list[ValidationIssue]]


def rule_not_empty(questionnaire: Questionnaire) -> list[ValidationIssue]:
    if questionnaire.questions:
        return []
    return [ValidationIssue(code="EMPTY", message="Questionnaire has no questions")]


def rule_unique_ids(questionnaire: Questionnaire) -> list[ValidationIssue]:
    counts = Counter(questionnaire.question_ids())
    return [
        ValidationIssue(
            code="DUPLICATE_ID", message=f"Duplicate question id '{qid}'", question_id=qid
        )
        for qid, count in counts.items()
        if count > 1
    ]


def rule_unique_labels(questionnaire: Questionnaire) -> list[ValidationIssue]:
    counts = Counter(question.label.strip().lower() for question in questionnaire.questions)
    return [
        ValidationIssue(
            code="DUPLICATE_LABEL",
            message=f"Question text repeated: '{label}'",
            severity=Severity.WARNING,
        )
        for label, count in counts.items()
        if count > 1
    ]


def rule_dependencies_reference_earlier_questions(
    questionnaire: Questionnaire,
) -> list[ValidationIssue]:
    """Skip logic may only depend on questions that appear earlier (prevents cycles)."""
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for question in questionnaire.questions:
        for rule in question.dependency_rules:
            if rule.depends_on not in seen:
                message = f"'{question.id}' depends on unknown/later question '{rule.depends_on}'"
                issues.append(
                    ValidationIssue(
                        code="INVALID_DEPENDENCY", message=message, question_id=question.id
                    )
                )
        seen.add(question.id)
    return issues


DEFAULT_RULES: tuple[ValidationRule, ...] = (
    rule_not_empty,
    rule_unique_ids,
    rule_unique_labels,
    rule_dependencies_reference_earlier_questions,
)


def validate_questionnaire(
    questionnaire: Questionnaire, rules: Sequence[ValidationRule] = DEFAULT_RULES
) -> ValidationReport:
    issues = [issue for rule in rules for issue in rule(questionnaire)]
    return ValidationReport(issues=tuple(issues))


def repair_questionnaire(questionnaire: Questionnaire) -> tuple[Questionnaire, int]:
    """Safe, deterministic fixes: drop duplicate questions and skip-logic rules that point at
    unknown or later questions. Returns the repaired questionnaire and the number of repairs."""
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
