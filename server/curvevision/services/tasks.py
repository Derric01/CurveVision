"""Tasks, job generation and the job state machine."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from curvevision.core.db import utcnow
from curvevision.core.errors import ConflictError, NotFoundError, ValidationError
from curvevision.domain.enums import JobKind, JobState, MediaKind, TaskStatus
from curvevision.domain.identity import User
from curvevision.domain.media import Asset
from curvevision.domain.task import Job, Task

#: Which transitions the job state machine allows.
#:
#:   new ──► in_progress ──► submitted ──► accepted
#:            ▲                   │
#:            └──── rejected ◄────┘
ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.NEW: frozenset({JobState.IN_PROGRESS, JobState.SUBMITTED}),
    JobState.IN_PROGRESS: frozenset({JobState.NEW, JobState.SUBMITTED}),
    JobState.SUBMITTED: frozenset({JobState.ACCEPTED, JobState.REJECTED, JobState.IN_PROGRESS}),
    JobState.REJECTED: frozenset({JobState.IN_PROGRESS, JobState.SUBMITTED}),
    JobState.ACCEPTED: frozenset({JobState.IN_PROGRESS}),
}


async def create_task(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    name: str,
    description: str | None,
    media_kind: MediaKind,
    segment_size: int,
    overlap: int,
    assignee_id: uuid.UUID | None,
    owner: User,
) -> Task:
    if overlap and segment_size and overlap >= segment_size:
        raise ValidationError("overlap must be smaller than segment_size")

    task = Task(
        project_id=project_id,
        name=name,
        description=description,
        media_kind=media_kind,
        segment_size=segment_size,
        overlap=overlap,
        assignee_id=assignee_id,
        owner_id=owner.id,
        status=TaskStatus.DRAFT,
    )
    session.add(task)
    await session.flush()
    return task


async def get_task(session: AsyncSession, task_id: uuid.UUID) -> Task:
    task = await session.get(Task, task_id)
    if task is None:
        raise NotFoundError("Task not found")
    return task


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found")
    return job


def plan_job_ranges(frame_count: int, segment_size: int, overlap: int) -> list[tuple[int, int]]:
    """Split ``frame_count`` frames into inclusive ``(start, stop)`` job ranges.

    ``segment_size`` of 0 means one job for the whole task. ``overlap`` repeats the last N
    frames at the start of the next job, which keeps tracks continuous across the seam.
    """
    if frame_count <= 0:
        return []
    if segment_size <= 0:
        return [(0, frame_count - 1)]

    step = max(1, segment_size - overlap)
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < frame_count:
        stop = min(start + segment_size - 1, frame_count - 1)
        ranges.append((start, stop))
        if stop >= frame_count - 1:
            break
        start += step
    return ranges


async def rebuild_jobs(session: AsyncSession, task: Task) -> list[Job]:
    """(Re)generate a task's jobs to match its current frame count.

    Refuses to run once annotation work exists: silently reshuffling frame ranges under
    annotators would orphan their annotations.
    """
    existing = list(
        (await session.execute(select(Job).where(Job.task_id == task.id))).scalars().all()
    )
    if any(
        job.shape_count or job.track_count or job.tag_count or job.state is not JobState.NEW
        for job in existing
    ):
        raise ConflictError(
            "This task already has annotation work; its jobs cannot be regenerated."
        )

    for job in existing:
        await session.delete(job)
    await session.flush()

    jobs = [
        Job(
            task_id=task.id,
            index=index,
            kind=JobKind.ANNOTATION,
            state=JobState.NEW,
            start_frame=start,
            stop_frame=stop,
            assignee_id=task.assignee_id,
        )
        for index, (start, stop) in enumerate(
            plan_job_ranges(task.frame_count, task.segment_size, task.overlap)
        )
    ]
    session.add_all(jobs)
    await session.flush()
    return jobs


async def recount_frames(session: AsyncSession, task: Task) -> int:
    """Recompute ``task.frame_count`` from its assets and reindex their frame offsets."""
    result = await session.execute(
        select(Asset).where(Asset.task_id == task.id).order_by(Asset.position)
    )
    assets = list(result.scalars().all())
    cursor = 0
    for asset in assets:
        asset.start_frame = cursor
        cursor += max(1, asset.frame_count)
    task.frame_count = cursor
    await session.flush()
    return cursor


async def transition_job(
    session: AsyncSession,
    job: Job,
    new_state: JobState,
    *,
    actor: User,
) -> Job:
    if job.state is new_state:
        return job
    allowed = ALLOWED_TRANSITIONS.get(job.state, frozenset())
    if new_state not in allowed:
        raise ConflictError(
            f"A job in state {job.state.value!r} cannot move to {new_state.value!r}"
        )

    job.state = new_state
    if new_state is JobState.SUBMITTED:
        job.submitted_at = utcnow()
    if new_state in (JobState.ACCEPTED, JobState.REJECTED):
        job.reviewed_at = utcnow()
        job.reviewer_id = job.reviewer_id or actor.id
    await session.flush()
    await _sync_task_status(session, job.task_id)
    return job


async def _sync_task_status(session: AsyncSession, task_id: uuid.UUID) -> None:
    """Keep the task's rolled-up status consistent with its jobs."""
    task = await session.get(Task, task_id)
    if task is None or task.status is TaskStatus.ARCHIVED:
        return

    rows = (
        await session.execute(
            select(Job.state, func.count()).where(Job.task_id == task_id).group_by(Job.state)
        )
    ).all()
    counts = {JobState(state): int(count) for state, count in rows}
    total = sum(counts.values())
    if total == 0:
        return

    if counts.get(JobState.ACCEPTED, 0) == total:
        task.status = TaskStatus.COMPLETED
    elif counts.get(JobState.NEW, 0) == total:
        task.status = TaskStatus.READY
    else:
        task.status = TaskStatus.IN_PROGRESS


async def task_progress(session: AsyncSession, task: Task) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(Job.state, func.count(), func.sum(Job.stop_frame - Job.start_frame + 1))
            .where(Job.task_id == task.id)
            .group_by(Job.state)
        )
    ).all()

    jobs_by_state = {state.value: 0 for state in JobState}
    frames_by_state: dict[str, int] = {state.value: 0 for state in JobState}
    for state, count, frames in rows:
        jobs_by_state[str(state)] = int(count)
        frames_by_state[str(state)] = int(frames or 0)

    accepted = frames_by_state[JobState.ACCEPTED.value]
    # Work sitting in review is real progress, but it is not finished; counting it at half
    # keeps the progress bar honest in both directions.
    submitted = frames_by_state[JobState.SUBMITTED.value]
    completed = accepted + submitted // 2
    total = max(task.frame_count, 1)

    return {
        "task_id": task.id,
        "job_count": sum(jobs_by_state.values()),
        "jobs_by_state": jobs_by_state,
        "completed_frames": accepted,
        "total_frames": task.frame_count,
        "completion": min(1.0, completed / total),
    }


async def list_task_jobs(session: AsyncSession, task_id: uuid.UUID) -> list[Job]:
    result = await session.execute(
        select(Job)
        .where(Job.task_id == task_id)
        .order_by(Job.index)
        .options(selectinload(Job.task))
    )
    return list(result.scalars().all())
