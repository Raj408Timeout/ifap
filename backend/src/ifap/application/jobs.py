"""Background generation jobs: start now, wait a bounded time, poll for the rest.

MCP hosts (and HTTP gateways) cancel calls after a fixed timeout - Claude Desktop gives up
after roughly a minute - while a workflow on a local LLM can take longer. Callers therefore
*start* a job, wait at most `wait_seconds` for it, and poll with the job id if it is still
running. The job keeps the latest workflow state, so a poll reports which agents have
finished so far.

Jobs live in process memory (bounded by `max_jobs`); persisting them is part of the
checkpointing work planned with Postgres.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from ifap.application.ports import QuestionnaireGenerator
from ifap.application.workflow import (
    AgentTrace,
    GenerationOutcome,
    GenerationRequest,
    WorkflowState,
)
from ifap.domain.errors import NotFoundError


class JobStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    status: JobStatus
    elapsed_seconds: float
    completed_steps: tuple[AgentTrace, ...]
    outcome: GenerationOutcome | None = None
    error: str | None = None


@dataclass(slots=True)
class _Job:
    id: str
    task: asyncio.Task[GenerationOutcome]
    started: float
    finished: float | None = None
    progress: tuple[AgentTrace, ...] = field(default=())


class GenerationJobService:
    def __init__(
        self,
        generator: QuestionnaireGenerator,
        *,
        max_jobs: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._generator = generator
        self._max_jobs = max_jobs
        self._clock = clock
        self._jobs: dict[str, _Job] = {}

    def start(self, request: GenerationRequest) -> str:
        job_id = uuid4().hex[:12]

        def on_step(state: WorkflowState) -> None:
            # Registered below before the task first runs; running jobs are never evicted.
            self._jobs[job_id].progress = state.trace

        task = asyncio.create_task(self._generator.generate(request, on_step))
        job = _Job(id=job_id, task=task, started=self._clock())
        task.add_done_callback(lambda _: self._finish(job))
        self._jobs[job_id] = job
        self._evict()
        return job_id

    async def wait(self, job_id: str, max_wait_seconds: float) -> JobSnapshot:
        """Wait up to `max_wait_seconds` for the job to finish; never cancels it."""
        job = self._job(job_id)
        if not job.task.done():
            await asyncio.wait({job.task}, timeout=max_wait_seconds)
        return self._snapshot(job)

    def _job(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError(
                f"Unknown job '{job_id}' (jobs are kept in memory and lost on server restart)"
            )
        return job

    def _finish(self, job: _Job) -> None:
        job.finished = self._clock()

    def _snapshot(self, job: _Job) -> JobSnapshot:
        end = job.finished if job.finished is not None else self._clock()
        elapsed = round(end - job.started, 1)
        if not job.task.done():
            return JobSnapshot(
                job_id=job.id,
                status=JobStatus.RUNNING,
                elapsed_seconds=elapsed,
                completed_steps=job.progress,
            )
        error = job.task.exception()
        if error is not None:
            return JobSnapshot(
                job_id=job.id,
                status=JobStatus.FAILED,
                elapsed_seconds=elapsed,
                completed_steps=job.progress,
                error=f"{type(error).__name__}: {error}",
            )
        outcome = job.task.result()
        return JobSnapshot(
            job_id=job.id,
            status=JobStatus.SUCCEEDED,
            elapsed_seconds=elapsed,
            completed_steps=outcome.trace,
            outcome=outcome,
        )

    def _evict(self) -> None:
        """Drop the oldest finished jobs beyond `max_jobs` (running jobs are never dropped)."""
        finished = [job_id for job_id, job in self._jobs.items() if job.task.done()]
        for job_id in finished[: max(0, len(self._jobs) - self._max_jobs)]:
            del self._jobs[job_id]
