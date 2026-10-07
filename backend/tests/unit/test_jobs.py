"""Background generation jobs: bounded waits, progress, failures, eviction."""

from __future__ import annotations

import asyncio

import pytest

from ifap.application.jobs import GenerationJobService, JobStatus
from ifap.application.ports import StepObserver
from ifap.application.workflow import (
    AgentStatus,
    AgentTrace,
    GenerationOutcome,
    GenerationRequest,
    WorkflowState,
)
from ifap.domain.errors import AgentExecutionError, NotFoundError
from ifap.domain.intent import BusinessIntent
from ifap.domain.questionnaire import AnswerType, Question, Questionnaire
from ifap.domain.validation import ValidationReport

STEP = AgentTrace(
    agent="intent", version="1", status=AgentStatus.SUCCEEDED, attempts=1, duration_ms=5.0
)


def _outcome() -> GenerationOutcome:
    question = Question(id="q1", label="Yes or no?", category="c", answer_type=AnswerType.BOOLEAN)
    return GenerationOutcome(
        questionnaire=Questionnaire(title="Done", survey_type="x", questions=(question,)),
        intent=BusinessIntent(raw_request="r", survey_type="x"),
        validation=ValidationReport(),
        trace=(STEP,),
        source_count=1,
        workflow="standard",
    )


class GatedGenerator:
    """Reports one step, then blocks until released (or fails if told to)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.release = asyncio.Event()
        self._fail = fail

    async def generate(
        self, request: GenerationRequest, on_step: StepObserver | None = None
    ) -> GenerationOutcome:
        if on_step is not None:
            on_step(WorkflowState(request=request, trace=(STEP,)))
        await self.release.wait()
        if self._fail:
            raise AgentExecutionError("builder halted")
        return _outcome()


REQUEST = GenerationRequest(message="slow request")


async def test_running_job_reports_progress_then_result() -> None:
    generator = GatedGenerator()
    jobs = GenerationJobService(generator, max_jobs=5)
    job_id = jobs.start(REQUEST)
    running = await jobs.wait(job_id, max_wait_seconds=0.01)
    assert running.status is JobStatus.RUNNING
    assert running.completed_steps == (STEP,)
    assert running.outcome is None

    generator.release.set()
    done = await jobs.wait(job_id, max_wait_seconds=1)
    assert done.status is JobStatus.SUCCEEDED
    assert done.outcome is not None
    assert done.outcome.questionnaire.title == "Done"
    assert (await jobs.wait(job_id, max_wait_seconds=0)).status is JobStatus.SUCCEEDED


async def test_failed_job_reports_error() -> None:
    generator = GatedGenerator(fail=True)
    jobs = GenerationJobService(generator, max_jobs=5)
    job_id = jobs.start(REQUEST)
    generator.release.set()
    failed = await jobs.wait(job_id, max_wait_seconds=1)
    assert failed.status is JobStatus.FAILED
    assert failed.error == "AgentExecutionError: builder halted"


async def test_unknown_job_raises() -> None:
    jobs = GenerationJobService(GatedGenerator(), max_jobs=5)
    with pytest.raises(NotFoundError, match="lost on server restart"):
        await jobs.wait("nope", max_wait_seconds=0)


async def test_finished_jobs_are_evicted_but_running_jobs_kept() -> None:
    done_gen, slow_gen = GatedGenerator(), GatedGenerator()
    done_gen.release.set()
    finished = GenerationJobService(done_gen, max_jobs=1)
    first = finished.start(REQUEST)
    await finished.wait(first, max_wait_seconds=1)
    second = finished.start(REQUEST)  # evicts the finished first job
    with pytest.raises(NotFoundError):
        await finished.wait(first, max_wait_seconds=0)
    assert (await finished.wait(second, max_wait_seconds=1)).status is JobStatus.SUCCEEDED

    running = GenerationJobService(slow_gen, max_jobs=1)
    a, b = running.start(REQUEST), running.start(REQUEST)
    assert (await running.wait(a, max_wait_seconds=0)).status is JobStatus.RUNNING
    assert (await running.wait(b, max_wait_seconds=0)).status is JobStatus.RUNNING
    slow_gen.release.set()
    await running.wait(a, max_wait_seconds=1)
    await running.wait(b, max_wait_seconds=1)
