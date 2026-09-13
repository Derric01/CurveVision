"""Producing quality reports.

`comparison.py` does the measuring; this decides *what* to measure and records the answer.
Two things here are judgement rather than arithmetic, and both are the kind of thing that
quietly makes a quality number meaningless:

**Only frames the ground truth actually covers are scored.** A ground-truth job over frames
0-49 says nothing about frame 300. Scoring the annotator's unchecked work as correct
inflates the number, and it inflates it in the direction that makes a team trust bad data.

**Tracks are flattened to their interpolated position on each frame.** An annotator working
with tracks and a reviewer working with shapes should score the same; comparing a track
against a shape by identity rather than by position would score them at zero.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.errors import ConflictError, NotFoundError, ValidationError
from curvevision.domain.annotation import Shape, Track, TrackShape
from curvevision.domain.enums import JobKind, ShapeType
from curvevision.domain.review import QualityReport
from curvevision.domain.task import Job, Task
from curvevision.services import interpolation
from curvevision.services.annotations import track_shape_to_keyframe
from curvevision.services.comparison import ComparisonResult, compare_annotations


@dataclass(frozen=True, slots=True)
class _Comparable:
    """A shape as the comparison sees it, whether it began as one or as a track position."""

    id: uuid.UUID
    frame: int
    label_id: uuid.UUID
    shape_type: ShapeType
    points: list[float]


async def comparable_shapes(session: AsyncSession, job: Job) -> list[_Comparable]:
    """Everything a job says is on screen, frame by frame.

    Plain shapes pass through. Tracks are evaluated at every frame in the job's range, so a
    track annotated with two keyframes contributes a position on each frame between them —
    which is what the annotator actually asserted, and what the exporter would write.
    """
    shapes = (await session.execute(select(Shape).where(Shape.job_id == job.id))).scalars().all()
    comparables = [
        _Comparable(
            id=shape.id,
            frame=shape.frame,
            label_id=shape.label_id,
            shape_type=shape.shape_type,
            points=list(shape.points),
        )
        for shape in shapes
    ]

    tracks = (await session.execute(select(Track).where(Track.job_id == job.id))).scalars().all()
    for track in tracks:
        keyframes = (
            (
                await session.execute(
                    select(TrackShape)
                    .where(TrackShape.track_id == track.id)
                    .order_by(TrackShape.frame)
                )
            )
            .scalars()
            .all()
        )
        if not keyframes:
            continue
        positions = [track_shape_to_keyframe(keyframe) for keyframe in keyframes]
        for frame in range(job.start_frame, job.stop_frame + 1):
            position = interpolation.interpolate_track(positions, track.shape_type, frame)
            if position is None:
                continue
            comparables.append(
                _Comparable(
                    # Deterministic per (track, frame): the comparison needs a stable
                    # identity to avoid matching one track position twice.
                    id=uuid.uuid5(track.id, str(frame)),
                    frame=frame,
                    label_id=track.label_id,
                    shape_type=track.shape_type,
                    points=list(position.points),
                )
            )
    return comparables


def overlapping_frames(job: Job, ground_truth: Job) -> list[int]:
    """Frames both jobs cover. Anything else is not evidence about the annotator."""
    start = max(job.start_frame, ground_truth.start_frame)
    stop = min(job.stop_frame, ground_truth.stop_frame)
    return list(range(start, stop + 1)) if stop >= start else []


async def find_ground_truth_job(session: AsyncSession, task_id: uuid.UUID) -> Job | None:
    result = await session.execute(
        select(Job)
        .where(Job.task_id == task_id, Job.kind == JobKind.GROUND_TRUTH)
        .order_by(Job.index)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_ground_truth_job(
    session: AsyncSession,
    task: Task,
    *,
    start_frame: int | None = None,
    stop_frame: int | None = None,
    assignee_id: uuid.UUID | None = None,
) -> Job:
    """Add the job that holds a task's ground truth.

    A ground-truth job is an ordinary job in every respect except what it means: a reviewer
    annotates it as carefully as they can, and every other job on the task is then scored
    against it. It is deliberately a separate job rather than a flag on existing work, so
    the ground truth has its own annotations, its own history and its own assignee.

    One per task. `find_ground_truth_job` takes the lowest-indexed one, so a second would
    silently decide every score on the task — better to refuse and let the caller delete the
    first, which makes the replacement a visible act.

    The range defaults to the whole task. Narrowing it is the normal case for long videos:
    checking 50 frames properly beats checking 5,000 carelessly, and `build_report` only
    ever scores the frames this job covers.
    """
    if task.frame_count <= 0:
        raise ValidationError("This task has no frames yet, so there is nothing to check")

    existing = await find_ground_truth_job(session, task.id)
    if existing is not None:
        raise ConflictError(
            "This task already has a ground-truth job; delete it before creating another"
        )

    start = 0 if start_frame is None else start_frame
    stop = task.frame_count - 1 if stop_frame is None else stop_frame
    if stop < start:
        raise ValidationError(f"stop_frame {stop} is before start_frame {start}")
    if start < 0 or stop > task.frame_count - 1:
        raise ValidationError(
            f"The range {start}-{stop} is outside the task's frames (0-{task.frame_count - 1})"
        )

    highest = (
        (await session.execute(select(Job.index).where(Job.task_id == task.id).order_by(Job.index)))
        .scalars()
        .all()
    )
    job = Job(
        task_id=task.id,
        index=(max(highest) + 1) if highest else 0,
        kind=JobKind.GROUND_TRUTH,
        start_frame=start,
        stop_frame=stop,
        assignee_id=assignee_id,
    )
    session.add(job)
    await session.flush()
    return job


async def build_report(
    session: AsyncSession,
    task: Task,
    job: Job,
    *,
    ground_truth: Job | None = None,
    iou_threshold: float = 0.5,
) -> QualityReport:
    """Compare `job` against the task's ground truth and store the result.

    Replaces any previous report for the same job: a quality report is a statement about
    the job as it is now, and keeping stale ones alongside it invites reading the wrong one.
    The annotation history is where the past lives.
    """
    if job.kind is JobKind.GROUND_TRUTH:
        raise ValidationError("A ground-truth job cannot be compared against itself")

    ground_truth = ground_truth or await find_ground_truth_job(session, task.id)
    if ground_truth is None:
        raise NotFoundError(
            "This task has no ground-truth job, so there is nothing to measure against"
        )

    frames = overlapping_frames(job, ground_truth)
    if not frames:
        raise ValidationError(
            "The ground-truth job covers no frame this job covers, "
            f"so there is nothing to compare (job {job.start_frame}-{job.stop_frame}, "
            f"ground truth {ground_truth.start_frame}-{ground_truth.stop_frame})"
        )

    result = compare_annotations(
        await comparable_shapes(session, job),
        await comparable_shapes(session, ground_truth),
        iou_threshold=iou_threshold,
        frames=frames,
    )

    existing = (
        (await session.execute(select(QualityReport).where(QualityReport.job_id == job.id)))
        .scalars()
        .all()
    )
    for stale in existing:
        await session.delete(stale)

    report = QualityReport(
        task_id=task.id,
        job_id=job.id,
        ground_truth_job_id=ground_truth.id,
        iou_threshold=iou_threshold,
        precision=result.overall.precision,
        recall=result.overall.recall,
        f1=result.overall.f1,
        # What this score is a statement about. Read back alongside the job's version now,
        # it is the difference between "this job scores 0.4" and "this job scored 0.4 six
        # edits ago" — and a reviewer acts very differently on the two.
        annotation_version=job.annotation_version,
        details=result.as_details(),
    )
    session.add(report)
    await session.flush()
    return report


def summarise(result: ComparisonResult) -> str:
    """One line for a log or a job result, so a run says what it found."""
    return (
        f"{result.overall.matched} matched, {result.overall.missing} missing, "
        f"{result.overall.extra} extra over {result.compared_frames} frames "
        f"(F1 {result.overall.f1:.3f})"
    )
