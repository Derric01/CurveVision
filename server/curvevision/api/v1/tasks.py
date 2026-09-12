"""Task, media and job endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from curvevision.api.deps import (
    IdentityDep,
    JobScopeDep,
    ProjectScopeDep,
    SessionDep,
    SettingsDep,
    TaskScopeDep,
)
from curvevision.core.errors import NotFoundError, ValidationError
from curvevision.core.pagination import Page, PageParamsDep, paginate
from curvevision.domain.enums import JobState, MediaKind, TaskStatus
from curvevision.domain.identity import OrganizationMembership
from curvevision.domain.media import MediaBlob
from curvevision.domain.project import Project
from curvevision.domain.task import Job, Task
from curvevision.policy import Action, ResourceType
from curvevision.schemas.task import (
    AssetOut,
    FrameInfo,
    JobOut,
    JobReviewRequest,
    JobUpdate,
    TaskCreate,
    TaskDetail,
    TaskMediaMeta,
    TaskOut,
    TaskProgress,
    TaskUpdate,
)
from curvevision.services import media as media_service
from curvevision.services import tasks as task_service
from curvevision.storage import ObjectNotFoundError, get_storage

router = APIRouter(tags=["tasks"])


# ----------------------------------------------------------------------------- tasks


@router.get("/tasks", response_model=Page[TaskOut])
async def list_tasks(
    identity: IdentityDep,
    session: SessionDep,
    params: PageParamsDep,
    project_id: uuid.UUID | None = Query(default=None),
    assignee_id: uuid.UUID | None = Query(default=None),
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
) -> Page[TaskOut]:
    statement = select(Task)
    if not identity.principal.is_superuser:
        member_orgs = select(OrganizationMembership.organization_id).where(
            OrganizationMembership.user_id == identity.principal.user_id
        )
        visible_projects = select(Project.id).where(Project.organization_id.in_(member_orgs))
        statement = statement.where(Task.project_id.in_(visible_projects))
    if project_id is not None:
        statement = statement.where(Task.project_id == project_id)
    if assignee_id is not None:
        statement = statement.where(Task.assignee_id == assignee_id)
    if status_filter is not None:
        statement = statement.where(Task.status == status_filter)

    total, rows = await paginate(session, statement.order_by(Task.created_at.desc()), params)
    return Page(
        count=total,
        limit=params.limit,
        offset=params.offset,
        results=[TaskOut.model_validate(row) for row in rows],
    )


@router.post("/tasks", response_model=TaskDetail, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate, session: SessionDep, identity: IdentityDep
) -> TaskDetail:
    from curvevision.api.deps import project_scope  # local import avoids a cycle

    scope = await project_scope(session, identity, payload.project_id)
    scope.authorize(Action.CREATE, ResourceType.TASK)
    task = await task_service.create_task(
        session,
        project_id=payload.project_id,
        name=payload.name,
        description=payload.description,
        media_kind=payload.media_kind,
        segment_size=payload.segment_size,
        overlap=payload.overlap,
        assignee_id=payload.assignee_id,
        owner=identity.user,
    )
    await session.commit()
    return TaskDetail.model_validate(task)


@router.get("/tasks/{task_id}", response_model=TaskDetail)
async def read_task(scope: TaskScopeDep, session: SessionDep) -> TaskDetail:
    detail = TaskDetail.model_validate(scope.task)
    detail.progress = TaskProgress.model_validate(
        await task_service.task_progress(session, scope.task)
    )
    return detail


@router.patch("/tasks/{task_id}", response_model=TaskDetail)
async def update_task(payload: TaskUpdate, scope: TaskScopeDep, session: SessionDep) -> TaskDetail:
    scope.authorize(Action.UPDATE)
    task = scope.task
    if payload.name is not None:
        task.name = payload.name
    if payload.description is not None:
        task.description = payload.description
    if payload.status is not None:
        task.status = payload.status
    if payload.assignee_id is not None:
        scope.authorize(Action.ASSIGN)
        task.assignee_id = payload.assignee_id
    await session.commit()
    return TaskDetail.model_validate(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(scope: TaskScopeDep, session: SessionDep) -> None:
    scope.authorize(Action.DELETE)
    await session.delete(scope.task)
    await session.commit()


@router.get("/tasks/{task_id}/progress", response_model=TaskProgress)
async def task_progress(scope: TaskScopeDep, session: SessionDep) -> TaskProgress:
    return TaskProgress.model_validate(await task_service.task_progress(session, scope.task))


# ----------------------------------------------------------------------------- media


@router.post(
    "/tasks/{task_id}/assets", response_model=list[AssetOut], status_code=status.HTTP_201_CREATED
)
async def upload_assets(
    scope: TaskScopeDep,
    session: SessionDep,
    settings: SettingsDep,
    files: list[UploadFile] = File(...),
) -> list[AssetOut]:
    """Upload one or more media files and (re)generate the task's jobs.

    Suitable for images and modest videos. Files larger than the request-size limit use the
    resumable upload endpoints instead.
    """
    scope.authorize(Action.CREATE, ResourceType.ASSET)
    storage = get_storage(settings)
    created = []
    for upload in files:
        data = await upload.read()
        asset = await media_service.ingest_upload(
            session,
            settings,
            storage,
            scope.task,
            filename=upload.filename or "upload.bin",
            data=data,
        )
        created.append(asset)

    await task_service.recount_frames(session, scope.task)
    await task_service.rebuild_jobs(session, scope.task)
    if scope.task.status is TaskStatus.DRAFT and scope.task.frame_count:
        scope.task.status = TaskStatus.READY
    await session.commit()
    return [AssetOut.model_validate(asset) for asset in created]


@router.get("/tasks/{task_id}/assets", response_model=list[AssetOut])
async def list_assets(scope: TaskScopeDep, session: SessionDep) -> list[AssetOut]:
    assets = await media_service.list_assets(session, scope.task.id)
    return [AssetOut.model_validate(asset) for asset in assets]


@router.delete("/tasks/{task_id}/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: uuid.UUID, scope: TaskScopeDep, session: SessionDep) -> None:
    scope.authorize(Action.DELETE, ResourceType.ASSET)
    asset = await media_service.get_asset(session, scope.task.id, asset_id)
    await media_service.delete_asset(session, asset)
    await task_service.recount_frames(session, scope.task)
    await task_service.rebuild_jobs(session, scope.task)
    await session.commit()


@router.get("/tasks/{task_id}/media", response_model=TaskMediaMeta)
async def media_meta(scope: TaskScopeDep, settings: SettingsDep) -> TaskMediaMeta:
    return TaskMediaMeta(
        task_id=scope.task.id,
        media_kind=scope.task.media_kind,
        frame_count=scope.task.frame_count,
        frames_per_chunk=settings.frames_per_chunk,
        chunk_count=media_service.chunk_count(scope.task.frame_count, settings.frames_per_chunk),
    )


@router.get("/tasks/{task_id}/frames/{frame}", response_model=FrameInfo)
async def frame_info(frame: int, scope: TaskScopeDep, session: SessionDep) -> FrameInfo:
    asset, offset = await media_service.resolve_frame(session, scope.task.id, frame)
    blob = await session.get(MediaBlob, asset.blob_id)
    if blob is None:
        raise NotFoundError("Media is missing for this frame")
    return FrameInfo(
        frame=frame,
        asset_id=asset.id,
        name=asset.name,
        width=blob.width,
        height=blob.height,
        offset=offset,
        media_url=f"/api/v1/tasks/{scope.task.id}/frames/{frame}/data",
        thumbnail_url=(
            f"/api/v1/tasks/{scope.task.id}/frames/{frame}/thumbnail"
            if blob.thumbnail_key
            else None
        ),
    )


@router.get("/tasks/{task_id}/frames/{frame}/data")
async def frame_data(
    frame: int, scope: TaskScopeDep, session: SessionDep, settings: SettingsDep
) -> Response:
    """Stream one frame's bytes.

    Media is never served from a public bucket: this endpoint enforces the same permission
    check as the rest of the API, and redirects to a presigned URL when the backend can
    issue one.
    """
    asset, offset = await media_service.resolve_frame(session, scope.task.id, frame)
    blob = await session.get(MediaBlob, asset.blob_id)
    if blob is None:
        raise NotFoundError("Media is missing for this frame")

    storage = get_storage(settings)

    # A video blob is one file holding thousands of frames, so the bytes of the blob are
    # not the bytes of the frame. Decode the one that was asked for.
    if blob.kind is MediaKind.VIDEO:
        jpeg = await media_service.render_video_frame(blob, storage, offset)
        return Response(
            content=jpeg,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    if media_service.is_presignable(blob) and blob.storage_key is not None:
        presigned = storage.public_url(
            blob.storage_key, expires_in=settings.presigned_url_ttl_seconds
        )
        if presigned:
            return Response(status_code=307, headers={"Location": presigned})

    try:
        body = await media_service.open_blob(blob, storage)
    except ObjectNotFoundError as exc:
        raise NotFoundError("Media data is missing from storage") from exc
    return StreamingResponse(
        body,
        media_type=blob.content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/tasks/{task_id}/frames/{frame}/thumbnail")
async def frame_thumbnail(
    frame: int, scope: TaskScopeDep, session: SessionDep, settings: SettingsDep
) -> Response:
    asset, _ = await media_service.resolve_frame(session, scope.task.id, frame)
    blob = await session.get(MediaBlob, asset.blob_id)
    if blob is None or not blob.thumbnail_key:
        raise NotFoundError("No thumbnail for this frame")
    storage = get_storage(settings)
    data = await storage.get(blob.thumbnail_key)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


# ------------------------------------------------------------------------------ jobs


@router.get("/tasks/{task_id}/jobs", response_model=list[JobOut])
async def list_task_jobs(scope: TaskScopeDep, session: SessionDep) -> list[JobOut]:
    result = await session.execute(
        select(Job).where(Job.task_id == scope.task.id).order_by(Job.index)
    )
    return [JobOut.model_validate(job) for job in result.scalars().all()]


@router.get("/jobs", response_model=Page[JobOut])
async def list_jobs(
    identity: IdentityDep,
    session: SessionDep,
    params: PageParamsDep,
    assignee_id: uuid.UUID | None = Query(default=None),
    state: JobState | None = Query(default=None),
    mine: bool = Query(default=False, description="Only jobs assigned to the caller"),
) -> Page[JobOut]:
    statement = select(Job).options(selectinload(Job.task))
    if not identity.principal.is_superuser:
        member_orgs = select(OrganizationMembership.organization_id).where(
            OrganizationMembership.user_id == identity.principal.user_id
        )
        visible_projects = select(Project.id).where(Project.organization_id.in_(member_orgs))
        visible_tasks = select(Task.id).where(Task.project_id.in_(visible_projects))
        statement = statement.where(Job.task_id.in_(visible_tasks))
    if mine:
        statement = statement.where(Job.assignee_id == identity.principal.user_id)
    elif assignee_id is not None:
        statement = statement.where(Job.assignee_id == assignee_id)
    if state is not None:
        statement = statement.where(Job.state == state)

    total, rows = await paginate(session, statement.order_by(Job.created_at.desc()), params)
    return Page(
        count=total,
        limit=params.limit,
        offset=params.offset,
        results=[JobOut.model_validate(row) for row in rows],
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
async def read_job(scope: JobScopeDep) -> JobOut:
    return JobOut.model_validate(scope.job)


@router.patch("/jobs/{job_id}", response_model=JobOut)
async def update_job(
    payload: JobUpdate, scope: JobScopeDep, session: SessionDep, identity: IdentityDep
) -> JobOut:
    job = scope.job
    if payload.assignee_id is not None or payload.reviewer_id is not None:
        scope.authorize(Action.ASSIGN)
        if payload.assignee_id is not None:
            job.assignee_id = payload.assignee_id
        if payload.reviewer_id is not None:
            job.reviewer_id = payload.reviewer_id
    if payload.locked is not None:
        scope.authorize(Action.UPDATE)
        job.locked = payload.locked
    if payload.state is not None:
        if payload.state in (JobState.ACCEPTED, JobState.REJECTED):
            scope.authorize(Action.REVIEW)
        elif payload.state is JobState.SUBMITTED:
            scope.authorize(Action.SUBMIT)
        else:
            scope.authorize(Action.UPDATE)
        await task_service.transition_job(session, job, payload.state, actor=identity.user)
    await session.commit()
    return JobOut.model_validate(job)


@router.post("/jobs/{job_id}/review", response_model=JobOut)
async def review_job(
    payload: JobReviewRequest,
    scope: JobScopeDep,
    session: SessionDep,
    identity: IdentityDep,
) -> JobOut:
    """Accept or reject submitted work.

    Rejection optionally opens an issue carrying the reviewer's comment, so the annotator
    sees *why* it came back rather than just that it did.
    """
    scope.authorize(Action.REVIEW)
    if scope.job.state is not JobState.SUBMITTED:
        raise ValidationError("Only submitted jobs can be reviewed")

    new_state = JobState.ACCEPTED if payload.accepted else JobState.REJECTED
    await task_service.transition_job(session, scope.job, new_state, actor=identity.user)

    if payload.comment:
        from curvevision.domain.review import Comment, Issue

        issue = Issue(
            job_id=scope.job.id,
            frame=scope.job.start_frame,
            created_by_id=identity.user.id,
        )
        session.add(issue)
        await session.flush()
        session.add(Comment(issue_id=issue.id, author_id=identity.user.id, body=payload.comment))

    await session.commit()
    return JobOut.model_validate(scope.job)


@router.get("/projects/{project_id}/tasks", response_model=Page[TaskOut])
async def list_project_tasks(
    scope: ProjectScopeDep, session: SessionDep, params: PageParamsDep
) -> Page[TaskOut]:
    statement = select(Task).where(Task.project_id == scope.project.id)
    total, rows = await paginate(session, statement.order_by(Task.created_at.desc()), params)
    return Page(
        count=total,
        limit=params.limit,
        offset=params.offset,
        results=[TaskOut.model_validate(row) for row in rows],
    )


@router.get("/tasks/{task_id}/statistics")
async def task_statistics(scope: TaskScopeDep, session: SessionDep) -> dict[str, int]:
    counts = (
        await session.execute(
            select(
                func.coalesce(func.sum(Job.shape_count), 0),
                func.coalesce(func.sum(Job.track_count), 0),
                func.coalesce(func.sum(Job.tag_count), 0),
            ).where(Job.task_id == scope.task.id)
        )
    ).one()
    return {
        "shape_count": int(counts[0]),
        "track_count": int(counts[1]),
        "tag_count": int(counts[2]),
        "frame_count": scope.task.frame_count,
    }
