"""Dataset export, import and immutable versions."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, File, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from curvevision.api.deps import (
    IdentityDep,
    ProjectScopeDep,
    SessionDep,
    SettingsDep,
    TaskScopeDep,
)
from curvevision.core.config import Settings
from curvevision.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from curvevision.core.signing import signed_query, verify
from curvevision.domain.enums import BackgroundTaskState, DatasetVersionState, WebhookEvent
from curvevision.domain.system import BackgroundTask as BackgroundTaskRow
from curvevision.domain.system import DatasetVersion
from curvevision.domain.task import Job
from curvevision.formats import ZipImportSource, get_format
from curvevision.policy import Action, ResourceType
from curvevision.schemas.system import (
    BackgroundTaskOut,
    DatasetVersionCreate,
    DatasetVersionOut,
    DownloadLinkOut,
    ExportRequest,
    ImportResultOut,
    VersionExportRequest,
)
from curvevision.services import annotations as annotation_service
from curvevision.services import background as background_service
from curvevision.services import datasets as dataset_service
from curvevision.services import webhooks as webhook_service
from curvevision.storage import get_storage

router = APIRouter(tags=["datasets"])


def export_workdir(settings: Settings) -> Path:
    """A fresh directory for one export, removed once its archive has been sent."""
    root = settings.export_work_dir
    if root is not None:
        Path(root).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="curvevision-export-", dir=root))


def archive_response(archive: Path, workdir: Path, headers: dict[str, str]) -> FileResponse:
    """The archive, streamed from disk; the working directory goes once it has been sent."""
    return FileResponse(
        archive,
        media_type="application/zip",
        headers=headers,
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )


@router.post("/projects/{project_id}/export")
async def export_project(
    payload: ExportRequest,
    scope: ProjectScopeDep,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Export a project's annotations as a downloadable archive.

    Synchronous: 10,000 frames without images take well under a second, and with images
    the archive is assembled on disk and streamed, so memory stays flat however many
    gigabytes it carries (see `export_dataset_file`).
    """
    scope.authorize(Action.EXPORT)
    fmt = get_format(payload.format)
    workdir = export_workdir(settings)
    try:
        archive, warnings = await dataset_service.export_dataset_file(
            session,
            scope.project,
            workdir=workdir,
            format_id=payload.format,
            task_ids=payload.task_ids or None,
            only_accepted=payload.only_accepted,
            include_images=payload.include_images,
            storage=get_storage(settings) if payload.include_images else None,
        )
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    filename = f"{scope.project.slug}-{payload.format}.{fmt.extension}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if warnings:
        # Surfaced in a header as well as inside the archive, so a scripted caller can see
        # that something was dropped without unzipping the result.
        headers["X-CurveVision-Warnings"] = " | ".join(warnings)[:900]
    return archive_response(archive, workdir, headers)


# ---------------------------------------------------------- exports too large to buffer
#
# `POST /projects/{id}/export` answers with the archive itself, which a page has to hold in
# memory before it can save it. Fine for annotations; not for an export carrying 10,000
# photographs. These routes run the same export as a background task, then hand the
# browser a short-lived signed link, which its own download manager streams to disk.

#: How long a download link works. Long enough to click, short enough not to be a credential.
DOWNLOAD_LINK_SECONDS = 600
DOWNLOAD_PURPOSE = "export-download"


@router.post(
    "/projects/{project_id}/exports",
    response_model=BackgroundTaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_export(
    payload: ExportRequest, scope: ProjectScopeDep, session: SessionDep
) -> BackgroundTaskOut:
    """Start an export in the background. Poll `GET /background-tasks/{id}` for it."""
    scope.authorize(Action.EXPORT)
    fmt = get_format(payload.format)
    if not fmt.capabilities.supports_export:
        raise ValidationError(f"Format {payload.format!r} does not support export")
    queued = await background_service.enqueue(
        session,
        kind="dataset.export",
        payload={
            "project_id": str(scope.project.id),
            "format": payload.format,
            "task_ids": [str(task_id) for task_id in payload.task_ids],
            "only_accepted": payload.only_accepted,
            "include_images": payload.include_images,
            "filename": f"{scope.project.slug}-{payload.format}.{fmt.extension}",
        },
        created_by_id=scope.identity.user.id,
        resource_type="project",
        resource_id=scope.project.id,
    )
    await session.refresh(queued)
    return BackgroundTaskOut.model_validate(queued)


@router.post("/exports/{task_id}/link", response_model=DownloadLinkOut)
async def export_download_link(
    task_id: uuid.UUID, identity: IdentityDep, session: SessionDep, settings: SettingsDep
) -> DownloadLinkOut:
    """A link that downloads a finished export, for ten minutes, without the auth header."""
    task = await session.get(BackgroundTaskRow, task_id)
    if task is None or task.kind != "dataset.export":
        raise NotFoundError("Export not found")
    if not identity.principal.is_superuser and task.created_by_id != identity.principal.user_id:
        raise PermissionDeniedError("This export belongs to another user")
    if task.state is not BackgroundTaskState.SUCCEEDED or not task.result.get("storage_key"):
        raise ConflictError("This export has not finished")
    query = signed_query(settings.secret_key, DOWNLOAD_PURPOSE, str(task.id), DOWNLOAD_LINK_SECONDS)
    return DownloadLinkOut(
        url=f"/api/v1/exports/{task.id}/archive?{urlencode(query)}",
        expires_in=DOWNLOAD_LINK_SECONDS,
    )


@router.get("/exports/{task_id}/archive")
async def download_export(
    task_id: uuid.UUID,
    expires: int,
    signature: str,
    session: SessionDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """The archive, streamed. Opened by the signed link, so it takes no auth header."""
    if not verify(settings.secret_key, DOWNLOAD_PURPOSE, str(task_id), expires, signature):
        raise PermissionDeniedError("This download link is not valid, or it has expired")
    task = await session.get(BackgroundTaskRow, task_id)
    if task is None or not task.result.get("storage_key"):
        raise NotFoundError("Export not found")
    filename = str(task.payload.get("filename") or "export.zip")
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if task.result.get("size_bytes"):
        headers["Content-Length"] = str(task.result["size_bytes"])
    if task.result.get("warnings"):
        headers["X-CurveVision-Warnings"] = " | ".join(task.result["warnings"])[:900]
    storage = get_storage(settings)
    return StreamingResponse(
        storage.stream(str(task.result["storage_key"])),
        media_type="application/zip",
        headers=headers,
    )


@router.post("/tasks/{task_id}/import", response_model=ImportResultOut)
async def import_annotations(
    scope: TaskScopeDep,
    session: SessionDep,
    format: str,
    file: UploadFile = File(...),
    conflict_policy: str = "append",
    create_missing_labels: bool = True,
) -> ImportResultOut:
    """Import annotations from an archive into a task's jobs."""
    scope.authorize(Action.IMPORT)
    if conflict_policy not in ("append", "replace"):
        raise ValidationError("conflict_policy must be 'append' or 'replace'")

    fmt = get_format(format)
    if not fmt.capabilities.supports_import:
        raise ValidationError(f"Format {format!r} does not support import")

    data = await file.read()
    try:
        source = ZipImportSource(data)
    except Exception as exc:
        raise ValidationError(f"Could not read the uploaded archive: {exc}") from exc

    context = await dataset_service.build_import_context(
        session, scope.task, create_missing_labels=create_missing_labels
    )
    result = fmt.import_(source, context)

    # Create any labels the file referenced that the project does not have yet.
    label_ids = dict(context.known_labels)
    if create_missing_labels and result.new_labels:
        from curvevision.schemas.project import AttributeIn, LabelIn
        from curvevision.services import projects as project_service

        for spec in result.new_labels:
            if spec.name in label_ids:
                continue
            # With its joints, for a skeleton: without them, the label exists and none of
            # its imported skeletons has a joint to be stored under. And with the attributes
            # the file declares for it: the import keeps only declared attributes, so a label
            # made without them dropped every value it had just been given (iteration 86).
            label = await project_service.create_label(
                session,
                scope.project,
                LabelIn(
                    name=spec.name,
                    color=spec.color,
                    children=[LabelIn(name=keypoint) for keypoint in spec.keypoints],
                    skeleton_edges=[list(edge) for edge in spec.skeleton_edges],
                    attributes=[
                        AttributeIn(
                            name=attribute.name,
                            attribute_type=attribute.attribute_type,
                            values=list(attribute.values),
                            mutable=attribute.mutable,
                        )
                        for attribute in spec.attributes
                    ],
                ),
            )
            label_ids[spec.name] = str(label.id)
        await session.flush()

    jobs = list(
        (await session.execute(select(Job).where(Job.task_id == scope.task.id).order_by(Job.index)))
        .scalars()
        .all()
    )
    if not jobs:
        raise ValidationError("This task has no jobs yet; upload media first")

    if conflict_policy == "replace":
        for job in jobs:
            await annotation_service.clear_annotations(session, job)

    written = await dataset_service.write_import(
        session,
        project_id=scope.project.id,
        jobs=jobs,
        result=result,
        label_ids=label_ids,
        actor_id=scope.identity.user.id,
    )
    await session.commit()

    return ImportResultOut(
        shapes_imported=written.shapes,
        tracks_imported=written.tracks,
        tags_imported=written.tags,
        frames_matched=result.frames_matched,
        labels_created=[spec.name for spec in result.new_labels],
        warnings=[*result.warnings, *written.warnings],
    )


# -------------------------------------------------------------------- dataset versions


@router.get("/projects/{project_id}/versions", response_model=list[DatasetVersionOut])
async def list_versions(scope: ProjectScopeDep, session: SessionDep) -> list[DatasetVersionOut]:
    scope.authorize(Action.VIEW, ResourceType.DATASET_VERSION)
    result = await session.execute(
        select(DatasetVersion)
        .where(DatasetVersion.project_id == scope.project.id)
        .order_by(DatasetVersion.created_at.desc())
    )
    return [DatasetVersionOut.model_validate(row) for row in result.scalars().all()]


@router.post(
    "/projects/{project_id}/versions",
    response_model=DatasetVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    payload: DatasetVersionCreate, scope: ProjectScopeDep, session: SessionDep
) -> DatasetVersionOut:
    scope.authorize(Action.CREATE, ResourceType.DATASET_VERSION)
    version = await dataset_service.create_dataset_version(
        session,
        scope.project,
        name=payload.name,
        description=payload.description,
        task_ids=payload.task_ids,
        created_by_id=scope.identity.user.id,
    )
    await session.commit()
    return DatasetVersionOut.model_validate(version)


@router.post(
    "/projects/{project_id}/versions/{version_id}/release",
    response_model=DatasetVersionOut,
)
async def release_version(
    version_id: uuid.UUID, scope: ProjectScopeDep, session: SessionDep, settings: SettingsDep
) -> DatasetVersionOut:
    """Seal a version: freeze its annotations. After this it can only be superseded."""
    scope.authorize(Action.UPDATE, ResourceType.DATASET_VERSION)
    version = await dataset_service.get_dataset_version(session, scope.project.id, version_id)
    await dataset_service.release_dataset_version(
        session, scope.project, version, get_storage(settings)
    )
    await session.commit()
    out = DatasetVersionOut.model_validate(version)
    await webhook_service.emit_for_project(
        session,
        WebhookEvent.DATASET_RELEASED,
        {"version": out.model_dump(mode="json")},
        scope.project,
        actor_id=scope.identity.user.id,
    )
    return out


@router.post("/projects/{project_id}/versions/{version_id}/export")
async def export_version(
    version_id: uuid.UUID,
    payload: VersionExportRequest,
    scope: ProjectScopeDep,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Export a dataset version. A released one exports its frozen annotations, however the
    project has changed since; a draft exports what releasing it now would freeze."""
    scope.authorize(Action.EXPORT)
    version = await dataset_service.get_dataset_version(session, scope.project.id, version_id)
    fmt = get_format(payload.format)
    workdir = export_workdir(settings)
    try:
        archive, warnings = await dataset_service.export_dataset_version_file(
            session,
            scope.project,
            version,
            workdir=workdir,
            format_id=payload.format,
            include_images=payload.include_images,
            storage=get_storage(settings),
        )
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in version.name)
    filename = f"{scope.project.slug}-{safe}-{payload.format}.{fmt.extension}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if version.content_hash:
        headers["X-CurveVision-Content-Hash"] = version.content_hash
    if warnings:
        headers["X-CurveVision-Warnings"] = " | ".join(warnings)[:900]
    return archive_response(archive, workdir, headers)


@router.get("/projects/{project_id}/versions/{version_id}", response_model=DatasetVersionOut)
async def read_version(
    version_id: uuid.UUID, scope: ProjectScopeDep, session: SessionDep
) -> DatasetVersionOut:
    scope.authorize(Action.VIEW, ResourceType.DATASET_VERSION)
    version = await dataset_service.get_dataset_version(session, scope.project.id, version_id)
    return DatasetVersionOut.model_validate(version)


@router.delete(
    "/projects/{project_id}/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_version(
    version_id: uuid.UUID, scope: ProjectScopeDep, session: SessionDep
) -> None:
    scope.authorize(Action.DELETE, ResourceType.DATASET_VERSION)
    version = await dataset_service.get_dataset_version(session, scope.project.id, version_id)
    if version.state is DatasetVersionState.RELEASED:
        raise ValidationError(
            "A released dataset version is immutable and cannot be deleted. "
            "Create a superseding version instead."
        )
    await session.delete(version)
    await session.commit()
