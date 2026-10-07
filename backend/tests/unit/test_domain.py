from __future__ import annotations

import pytest
from pydantic import ValidationError

from ifap.domain.errors import DomainRuleViolationError
from ifap.domain.knowledge import QuestionTemplate
from ifap.domain.questionnaire import (
    AnswerType,
    Choice,
    DependencyRule,
    Question,
    Questionnaire,
    QuestionnaireStatus,
)
from ifap.domain.validation import Severity, validate_questionnaire


def _question(qid: str, label: str | None = None, *, depends_on: str | None = None) -> Question:
    rules = (DependencyRule(depends_on=depends_on, value=True),) if depends_on else ()
    return Question(
        id=qid,
        label=label or f"Question {qid}?",
        category="General",
        answer_type=AnswerType.BOOLEAN,
        dependency_rules=rules,
    )


def test_choice_types_require_choices() -> None:
    with pytest.raises(ValidationError):
        Question(id="q1", label="Pick one", category="c", answer_type=AnswerType.SINGLE_CHOICE)


def test_non_choice_types_reject_choices() -> None:
    with pytest.raises(ValidationError):
        Question(
            id="q1",
            label="Yes or no?",
            category="c",
            answer_type=AnswerType.BOOLEAN,
            choices=(Choice(value="a", label="A"), Choice(value="b", label="B")),
        )


def test_revise_increments_version() -> None:
    questionnaire = Questionnaire(title="Survey", survey_type="x", questions=(_question("q1"),))
    revised = questionnaire.revise(title="New", description="d", questions=(_question("q2"),))
    assert revised.version == 2
    assert revised.question_ids() == ["q2"]
    assert revised.id == questionnaire.id


def test_published_questionnaire_is_immutable() -> None:
    published = Questionnaire(
        title="Survey", survey_type="x", questions=(_question("q1"),)
    ).publish()
    assert published.status is QuestionnaireStatus.PUBLISHED
    with pytest.raises(DomainRuleViolationError):
        published.revise(title="t", description="", questions=())


def test_cannot_publish_empty() -> None:
    with pytest.raises(DomainRuleViolationError):
        Questionnaire(title="Survey", survey_type="x").publish()


def test_validation_flags_duplicates_and_forward_dependencies() -> None:
    questionnaire = Questionnaire(
        title="Survey",
        survey_type="x",
        questions=(_question("q1", depends_on="q2"), _question("q2"), _question("q2", "Other?")),
    )
    report = validate_questionnaire(questionnaire)
    codes = {issue.code for issue in report.issues}
    assert {"DUPLICATE_ID", "INVALID_DEPENDENCY"} <= codes
    assert not report.is_valid


def test_duplicate_label_is_only_a_warning() -> None:
    questionnaire = Questionnaire(
        title="Survey",
        survey_type="x",
        questions=(_question("q1", "Same?"), _question("q2", "same?")),
    )
    report = validate_questionnaire(questionnaire)
    assert report.is_valid
    assert [issue.severity for issue in report.issues] == [Severity.WARNING]


def test_sample_dataset_shape(templates: list[QuestionTemplate]) -> None:
    assert len(templates) == 200
    names = {t.template_name for t in templates}
    assert names == {
        "Customer Satisfaction",
        "Employee Engagement",
        "Healthcare Assessment",
        "Product Feedback",
        "Compliance Review",
    }
    assert len({t.template_id for t in templates}) == 200
    assert {t.question.answer_type for t in templates} == set(AnswerType)


def test_empty_questionnaire_is_invalid() -> None:
    report = validate_questionnaire(Questionnaire(title="Survey", survey_type="x"))
    assert [issue.code for issue in report.issues] == ["EMPTY"]
    assert not report.is_valid
