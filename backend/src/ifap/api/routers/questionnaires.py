"""Questionnaire endpoints: generate (chat), review, revise, publish."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from ifap.api.dependencies import ContainerDep
from ifap.api.schemas import (
    GenerateQuestionnaireRequest,
    GenerateQuestionnaireResponse,
    QuestionnaireWithValidation,
    ReviseQuestionnaireRequest,
    ValidationSummary,
)
from ifap.application.workflow import GenerationRequest
from ifap.domain.questionnaire import Questionnaire
from ifap.domain.validation import validate_questionnaire

router = APIRouter(prefix="/questionnaires", tags=["questionnaires"])


@router.post(
    "/generate",
    response_model=GenerateQuestionnaireResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a questionnaire from a natural-language business need",
)
async def generate_questionnaire(
    body: GenerateQuestionnaireRequest, container: ContainerDep
) -> GenerateQuestionnaireResponse:
    request = GenerationRequest.model_validate(body.model_dump())
    outcome = await container.generator.generate(request)
    return GenerateQuestionnaireResponse.from_outcome(outcome)


@router.get("", response_model=list[Questionnaire])
async def list_questionnaires(
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Questionnaire]:
    return await container.questionnaires.list(limit=limit, offset=offset)


@router.get("/{questionnaire_id}", response_model=Questionnaire)
async def get_questionnaire(questionnaire_id: UUID, container: ContainerDep) -> Questionnaire:
    return await container.questionnaires.get(questionnaire_id)


@router.put("/{questionnaire_id}", response_model=QuestionnaireWithValidation)
async def revise_questionnaire(
    questionnaire_id: UUID, body: ReviseQuestionnaireRequest, container: ContainerDep
) -> QuestionnaireWithValidation:
    revised = await container.questionnaires.revise(
        questionnaire_id, title=body.title, description=body.description, questions=body.questions
    )
    report = validate_questionnaire(revised)
    return QuestionnaireWithValidation(
        questionnaire=revised, validation=ValidationSummary.from_report(report)
    )


@router.post("/{questionnaire_id}/publish", response_model=Questionnaire)
async def publish_questionnaire(questionnaire_id: UUID, container: ContainerDep) -> Questionnaire:
    return await container.questionnaires.publish(questionnaire_id)
