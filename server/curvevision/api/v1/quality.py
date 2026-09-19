"""Quality reports: how an annotated job scores against ground truth.

Computing a report is a **review** action, not a read. It measures somebody's work, and the
same rule that stops an annotator accepting their own job applies here: a score you can run
on yourself and nobody else can see is not a quality process. Reading one is an ordinary
view of the job it describes.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from curvevision.api.deps import JobScopeDep, SessionDep, TaskScopeDep
from curvevision.core.errors import NotFoundError
from curvevision.domain.review import QualityReport
from curvevision.policy import Action, ResourceType
from curvevision.schemas.system import GroundTruthJobIn, QualityReportOut, QualityRequest
from curvevision.schemas.task import JobOut
from curvevision.services import quality as quality_service

router = APIRouter(tags=["quality"])


@router.post("/tasks/{task_id}/ground-truth", response_model=JobOut, status_code=201)
async def create_ground_truth_job(
    payload: GroundTruthJobIn,
    scope: TaskScopeDep,
    session: SessionDep,
) -> JobOut:
    """Create the job whose annotations every other job on this task is measured against.

    Creating it is a task-level change rather than a review action: it decides what
    "correct" means for the task, which is the same weight of decision as defining labels.
    """
    scope.authorize(Action.UPDATE, ResourceType.TASK)
    job = await quality_service.create_ground_truth_job(
        session,
        scope.task,
        start_frame=payload.start_frame,
        stop_frame=payload.stop_frame,
        assignee_id=payload.assignee_id,
    )
    await session.commit()
    await session.refresh(job)
    return JobOut.model_validate(job)


@router.post("/jobs/{job_id}/quality", response_model=QualityReportOut)
async def compute_quality(
    payload: QualityRequest,
    scope: JobScopeDep,
    session: SessionDep,
) -> QualityReportOut:
    """Score this job against its task's ground-truth job.

    Runs inline rather than as a background job: the comparison is arithmetic over
    annotations already in the database, and a reviewer asking "how did this go" should get
    an answer rather than a task id to poll.

    **That used to be a guess about scale, and is now a measurement.** This docstring said a
    very large ground truth would change the calculus; measuring it
    (`tests/benchmarks/test_comparison_scale.py`) found the worry aimed at the wrong
    dimension. Frames are free -- 100,000 of them score in 4.6 seconds, linear -- and the
    cost was in *objects per frame*, where the matcher was quadratic. With the bounding-box
    rejection in `services/comparison.py` the worst realistic case, 200,000 shapes a side
    over 10,000 frames, finishes in 4.4 seconds, so there is nothing here a queue would
    improve.
    """
    scope.authorize(Action.REVIEW, ResourceType.JOB)
    report = await quality_service.build_report(
        session, scope.task, scope.job, iou_threshold=payload.iou_threshold
    )
    await session.commit()
    return QualityReportOut.model_validate(report)


@router.get("/jobs/{job_id}/quality", response_model=QualityReportOut)
async def job_quality(scope: JobScopeDep, session: SessionDep) -> QualityReportOut:
    """The most recent report for this job, or 404 when none has been computed."""
    scope.authorize(Action.VIEW, ResourceType.JOB)
    result = await session.execute(
        select(QualityReport)
        .where(QualityReport.job_id == scope.job.id)
        .order_by(QualityReport.created_at.desc())
        .limit(1)
    )
    report = result.scalar_one_or_none()
    if report is None:
        raise NotFoundError("No quality report has been computed for this job")
    return QualityReportOut.model_validate(report)


@router.get("/tasks/{task_id}/quality", response_model=list[QualityReportOut])
async def task_quality(scope: TaskScopeDep, session: SessionDep) -> list[QualityReportOut]:
    """Every job's latest score, for seeing a task's annotation quality as a whole."""
    scope.authorize(Action.VIEW, ResourceType.TASK)
    result = await session.execute(
        select(QualityReport)
        .where(QualityReport.task_id == scope.task.id)
        .order_by(QualityReport.created_at.desc())
    )
    return [QualityReportOut.model_validate(report) for report in result.scalars()]
