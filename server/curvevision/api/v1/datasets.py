"""Dataset export, import and immutable versions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Response, UploadFile, status
from sqlalchemy import select

from curvevision.api.deps import ProjectScopeDep, SessionDep, SettingsDep, TaskScopeDep
from curvevision.core.errors import ValidationError
from curvevision.domain.annotation import Shape, Tag
from curvevision.domain.enums import AnnotationSource, DatasetVersionState
from curvevision.domain.system import DatasetVersion
from curvevision.domain.task import Job
from curvevision.formats import ZipImportSource, get_format
from curvevision.policy import Action, ResourceType
from curvevision.schemas.system import (
    DatasetVersionCreate,
    DatasetVersionOut,
    ExportRequest,
)
from curvevision.services import annotations as annotation_service
from curvevision.services import datasets as dataset_service
from curvevision.storage import get_storage

router = APIRouter(tags=["datasets"])


@router.post("/projects/{project_id}/export")
async def export_project(
    payload: ExportRequest,
    scope: ProjectScopeDep,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Export a project's annotations as a downloadable archive.

    Synchronous, because the common case (a few thousand frames, no images) finishes in
    well under a second. Large exports with images belong on the background-task path,
    which is *In Progress*.
    """
    scope.authorize(Action.EXPORT)
    fmt = get_format(payload.format)
    archive, warnings = await dataset_service.export_dataset(
        session,
        scope.project,
        format_id=payload.format,
        task_ids=payload.task_ids or None,
        only_accepted=payload.only_accepted,
        include_images=payload.include_images,
        storage=get_storage(settings) if payload.include_images else None,
    )
    filename = f"{scope.project.slug}-{payload.format}.{fmt.extension}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if warnings:
        # Surfaced in a header as well as inside the archive, so a scripted caller can see
        # that something was dropped without unzipping the result.
        headers["X-CurveVision-Warnings"] = " | ".join(warnings)[:900]
    return Response(content=archive, media_type="application/zip", headers=headers)


@router.post("/tasks/{task_id}/import", status_code=status.HTTP_200_OK)
async def import_annotations(
    scope: TaskScopeDep,
    session: SessionDep,
    format: str,
    file: UploadFile = File(...),
    conflict_policy: str = "append",
    create_missing_labels: bool = True,
) -> dict[str, object]:
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
        from curvevision.schemas.project import LabelIn
        from curvevision.services import projects as project_service

        for spec in result.new_labels:
            if spec.name in label_ids:
                continue
            label = await project_service.create_label(
                session, scope.project, LabelIn(name=spec.name, color=spec.color)
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

    written = 0
    unmatched_labels: set[str] = set()
    for frame, record in result.shapes:
        target = _job_for_frame(jobs, frame)
        if target is None:
            continue
        label_id = label_ids.get(record.label)
        if label_id is None:
            unmatched_labels.add(record.label)
            continue
        session.add(
            Shape(
                job_id=target.id,
                label_id=uuid.UUID(label_id),
                frame=frame,
                shape_type=record.shape_type,
                points=record.points,
                rotation=record.rotation,
                occluded=record.occluded,
                z_order=record.z_order,
                group=record.group,
                source=AnnotationSource.IMPORTED,
                attributes={},
                mask=record.mask,
                elements=record.elements,
                created_by_id=scope.identity.user.id,
            )
        )
        written += 1

    tags_written = 0
    for frame, label_name in result.tags:
        target = _job_for_frame(jobs, frame)
        label_id = label_ids.get(label_name)
        if target is None or label_id is None:
            continue
        session.add(
            Tag(
                job_id=target.id,
                label_id=uuid.UUID(label_id),
                frame=frame,
                source=AnnotationSource.IMPORTED,
                attributes={},
                created_by_id=scope.identity.user.id,
            )
        )
        tags_written += 1

    await session.flush()
    for job in jobs:
        job.annotation_version += 1
        await annotation_service.refresh_counts(session, job)
    await session.commit()

    warnings = list(result.warnings)
    if unmatched_labels:
        warnings.append(
            "Skipped annotations for labels not present in the project: "
            + ", ".join(sorted(unmatched_labels))
        )

    return {
        "shapes_imported": written,
        "tags_imported": tags_written,
        "frames_matched": result.frames_matched,
        "labels_created": [spec.name for spec in result.new_labels],
        "warnings": warnings,
    }


def _job_for_frame(jobs: list[Job], frame: int) -> Job | None:
    for job in jobs:
        if job.contains_frame(frame):
            return job
    return None


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
    version_id: uuid.UUID, scope: ProjectScopeDep, session: SessionDep
) -> DatasetVersionOut:
    """Seal a version. After this it is immutable and can only be superseded."""
    scope.authorize(Action.UPDATE, ResourceType.DATASET_VERSION)
    version = await dataset_service.get_dataset_version(session, scope.project.id, version_id)
    await dataset_service.release_dataset_version(session, scope.project, version)
    await session.commit()
    return DatasetVersionOut.model_validate(version)


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
