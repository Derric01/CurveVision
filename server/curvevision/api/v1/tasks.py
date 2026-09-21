"""Task, media and job endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Header, Query, Request, Response, UploadFile, status
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
from curvevision.schemas.system import BackgroundTaskOut
from curvevision.schemas.task import (
    ESTIMATED_ASSET_SAMPLE,
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
    UploadInit,
    UploadSessionOut,
)
from curvevision.services import background as background_service
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

    await media_service.finish_ingestion(session, scope.task)
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


# ------------------------------------------------------------------- resumable uploads
#
# An offset-based ``PATCH`` protocol: create a session declaring a filename and size, send
# the bytes in as many chunks as the client likes, each stating the offset it believes it
# is resuming from, then complete it. A dropped connection loses at most the chunk in
# flight -- `GET` reports how much has actually landed so a client that lost track of its
# own progress can ask rather than guess.


@router.post(
    "/tasks/{task_id}/uploads",
    response_model=UploadSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_upload(
    payload: UploadInit,
    scope: TaskScopeDep,
    session: SessionDep,
    settings: SettingsDep,
) -> UploadSessionOut:
    scope.authorize(Action.CREATE, ResourceType.ASSET)
    upload_session = await media_service.create_upload_session(
        session,
        settings,
        scope.task,
        filename=payload.filename,
        size=payload.size,
        created_by_id=scope.identity.user.id,
    )
    await session.commit()
    return UploadSessionOut.model_validate(upload_session)


@router.get("/tasks/{task_id}/uploads/{upload_id}", response_model=UploadSessionOut)
async def get_upload(
    upload_id: uuid.UUID, scope: TaskScopeDep, session: SessionDep
) -> UploadSessionOut:
    """How many bytes this session has actually received, for a client resuming after a
    dropped connection or a restart to learn where it left off rather than guess.
    """
    scope.authorize(Action.CREATE, ResourceType.ASSET)
    upload_session = await media_service.get_upload_session(session, scope.task.id, upload_id)
    return UploadSessionOut.model_validate(upload_session)


@router.patch("/tasks/{task_id}/uploads/{upload_id}", response_model=UploadSessionOut)
async def append_upload(
    upload_id: uuid.UUID,
    request: Request,
    scope: TaskScopeDep,
    session: SessionDep,
    settings: SettingsDep,
    upload_offset: int = Header(..., alias="Upload-Offset"),
) -> UploadSessionOut:
    """Append one chunk. ``Upload-Offset`` must equal the number of bytes already received;
    a mismatch is a 409 naming the real offset rather than a silent corruption or a
    duplicated chunk.
    """
    scope.authorize(Action.CREATE, ResourceType.ASSET)
    storage = get_storage(settings)
    upload_session = await media_service.get_upload_session(session, scope.task.id, upload_id)
    data = await request.body()
    await media_service.append_upload_chunk(
        storage, upload_session, offset=upload_offset, data=data
    )
    await session.commit()
    return UploadSessionOut.model_validate(upload_session)


@router.post(
    "/tasks/{task_id}/uploads/{upload_id}/complete",
    response_model=AssetOut,
    status_code=status.HTTP_201_CREATED,
)
async def complete_upload(
    upload_id: uuid.UUID, scope: TaskScopeDep, session: SessionDep, settings: SettingsDep
) -> AssetOut:
    """Finalise a fully-received upload and attach it to the task as the next asset."""
    scope.authorize(Action.CREATE, ResourceType.ASSET)
    storage = get_storage(settings)
    upload_session = await media_service.get_upload_session(session, scope.task.id, upload_id)
    asset = await media_service.complete_upload(
        session, settings, storage, scope.task, upload_session
    )
    await media_service.finish_ingestion(session, scope.task)
    return AssetOut.model_validate(asset)


@router.delete("/tasks/{task_id}/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def abort_upload(
    upload_id: uuid.UUID, scope: TaskScopeDep, session: SessionDep, settings: SettingsDep
) -> None:
    scope.authorize(Action.DELETE, ResourceType.ASSET)
    storage = get_storage(settings)
    upload_session = await media_service.get_upload_session(session, scope.task.id, upload_id)
    await media_service.abort_upload(session, storage, upload_session)
    await session.commit()


@router.get("/tasks/{task_id}/media", response_model=TaskMediaMeta)
async def media_meta(
    scope: TaskScopeDep, session: SessionDep, settings: SettingsDep
) -> TaskMediaMeta:
    """Frame numbering and chunking for a task, and whether the frame count is trustworthy.

    `frame_count_exact` is the part a client must not ignore. A video task's count starts as
    an estimate from container metadata, and an overestimate offers frames that do not
    exist -- an annotator who steps onto one meets what looks like missing media. The probe
    normally fixes it within seconds of upload, but it can decline (the task already carries
    annotations) or fail (an undecodable file), and this is where that shows.
    """
    estimated = await media_service.estimated_assets(session, scope.task.id)
    return TaskMediaMeta(
        task_id=scope.task.id,
        media_kind=scope.task.media_kind,
        frame_count=scope.task.frame_count,
        frames_per_chunk=settings.frames_per_chunk,
        chunk_count=media_service.chunk_count(scope.task.frame_count, settings.frames_per_chunk),
        frame_count_exact=not estimated,
        estimated_assets=[asset.name for asset in estimated[:ESTIMATED_ASSET_SAMPLE]],
        estimated_asset_count=len(estimated),
    )


@router.post(
    "/tasks/{task_id}/media/recount",
    response_model=BackgroundTaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def recount_frames(scope: TaskScopeDep, session: SessionDep) -> BackgroundTaskOut:
    """Ask for a task's video frame counts to be established by decoding.

    The upload path already does this automatically; this endpoint exists for the cases
    where that attempt did not stick -- a file that was temporarily unreachable, a codec a
    later build can decode, or a task that carried annotations when the probe first ran and
    has since had them cleared. Without it a client can see that a count is provisional and
    has no way to act on it.

    Returns the queued job rather than a result: counting a two-hour clip is minutes of
    decoding, which is exactly why it was never on the request path. Poll the background
    task, or re-read `GET /tasks/{id}/media` and watch `frame_count_exact`.

    A task with nothing left to count returns a job anyway, which then finds every asset
    already exact and changes nothing. That is cheaper than teaching the endpoint a second
    answer shape for a case the caller can already see from `frame_count_exact`.
    """
    scope.authorize(Action.UPDATE, ResourceType.TASK)
    # `enqueue` commits the row itself -- it has to, because a worker cannot pick up a row
    # that is not visible yet -- so there is deliberately no commit after this.
    queued = await background_service.enqueue(
        session,
        kind="media.probe_task",
        payload={"task_id": str(scope.task.id)},
        resource_type="task",
        resource_id=scope.task.id,
    )
    return BackgroundTaskOut.model_validate(queued)


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
    # not the bytes of the frame. The service decides how to get it -- from a chunk, by
    # building one, or by decoding the single frame; see `media_service.video_frame_jpeg`.
    if blob.kind is MediaKind.VIDEO:
        jpeg = await media_service.video_frame_jpeg(
            session, settings, storage, scope.task, blob, frame, offset
        )
        # A chunk built while serving a read is worth keeping.
        await session.commit()
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


@router.get("/tasks/{task_id}/chunks/{chunk}")
async def chunk_data(
    chunk: int, scope: TaskScopeDep, session: SessionDep, settings: SettingsDep
) -> Response:
    """A run of decoded video frames, as one ZIP of JPEGs named by frame number.

    Built on first request and reused afterwards, because the decode it replaces is the
    slowest thing this server does. Fetching one chunk instead of 36 frame requests is the
    difference between scrubbing a video and waiting for it.

    404 for a task with no video in that range: an image task serves frames directly in
    constant time and gains nothing from an archive of its own pictures.
    """
    storage = get_storage(settings)
    built = await media_service.build_chunk(session, settings, storage, scope.task, chunk)
    if built is None:
        raise NotFoundError("This task has no video frames in that range")
    await session.commit()

    last = built.start_frame + built.frame_count - 1
    try:
        body = storage.stream(built.storage_key)
    except ObjectNotFoundError as exc:  # pragma: no cover - defensive
        raise NotFoundError("Chunk data is missing from storage") from exc
    return StreamingResponse(
        body,
        media_type=built.content_type,
        headers={
            "Cache-Control": "private, max-age=86400",
            # A chunk's contents are fixed by its frame range, so a client that has one
            # never needs to ask for it again.
            "X-CurveVision-Frames": f"{built.start_frame}-{last}",
        },
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
    # `model_fields_set` rather than a None check, because for these two fields the
    # difference matters: an omitted field means "leave it alone" and an explicit null means
    # "unassign". Reading both as None made a job assignable and never unassignable -- the
    # column is nullable, and nothing but this could put it back.
    assignment_fields = {"assignee_id", "reviewer_id"} & payload.model_fields_set
    if assignment_fields:
        scope.authorize(Action.ASSIGN)
        if "assignee_id" in assignment_fields:
            job.assignee_id = payload.assignee_id
        if "reviewer_id" in assignment_fields:
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

    if assignment_fields:
        # `assignee` and `reviewer` were loaded when the job was fetched, and the
        # sessionmaker is `expire_on_commit=False`, so writing the *id* leaves the loaded
        # relationship holding whoever was there before. Without this the response reports
        # the previous holder -- or `null` for a job that just gained its first one, which
        # is what a picker would render straight back at the person who just assigned it.
        await session.refresh(job, list(assignment_fields | {"assignee", "reviewer"}))
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
