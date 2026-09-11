"""Dataset export, import and immutable versions.

Export builds a ``DatasetView`` whose ``frames`` is a **generator**, so a 500k-image
project is never materialised in memory. Tracks are flattened into per-frame shapes here,
before the format sees them, so every format gets one uniform shape stream.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from curvevision.core.db import utcnow
from curvevision.core.errors import ConflictError, NotFoundError, ValidationError
from curvevision.domain.annotation import Shape, Tag, Track
from curvevision.domain.enums import (
    AnnotationSource,
    DatasetVersionState,
    JobState,
    ShapeType,
)
from curvevision.domain.media import Asset, MediaBlob
from curvevision.domain.project import Label, Project
from curvevision.domain.system import DatasetVersion
from curvevision.domain.task import Job, Task
from curvevision.formats import (
    DatasetView,
    FrameRecord,
    ImportContext,
    ImportResult,
    LabelSpec,
    ShapeRecord,
    ZipExportSink,
    get_format,
)
from curvevision.formats.base import AttributeSpec
from curvevision.services import media as media_service
from curvevision.services.interpolation import Keyframe, interpolate_track
from curvevision.storage import Storage

# ------------------------------------------------------------------------------ export


async def build_dataset_view(
    session: AsyncSession,
    project: Project,
    *,
    task_ids: list[uuid.UUID] | None = None,
    only_accepted: bool = False,
    include_images: bool = False,
    storage: Storage | None = None,
) -> tuple[DatasetView, list[ShapeType]]:
    """Assemble a streaming view over a project's annotations.

    Returns the view and the geometry types it contains, so the caller can warn about what
    the chosen format will drop *before* running the export.
    """
    labels = list(
        (
            await session.execute(
                select(Label)
                .where(Label.project_id == project.id)
                .options(selectinload(Label.attributes), selectinload(Label.children))
                .order_by(Label.position, Label.name)
            )
        )
        .scalars()
        .all()
    )
    label_names = {label.id: label.name for label in labels}

    task_query = select(Task).where(Task.project_id == project.id)
    if task_ids:
        task_query = task_query.where(Task.id.in_(task_ids))
    tasks = list((await session.execute(task_query.order_by(Task.created_at))).scalars().all())

    present_types: set[ShapeType] = set()
    frame_records: list[FrameRecord] = []

    for task in tasks:
        job_query = select(Job).where(Job.task_id == task.id)
        if only_accepted:
            job_query = job_query.where(Job.state == JobState.ACCEPTED)
        jobs = list((await session.execute(job_query.order_by(Job.index))).scalars().all())
        if not jobs:
            continue

        assets = list(
            (
                await session.execute(
                    select(Asset).where(Asset.task_id == task.id).order_by(Asset.position)
                )
            )
            .scalars()
            .all()
        )
        blobs = {
            blob.id: blob
            for blob in (
                await session.execute(
                    select(MediaBlob).where(MediaBlob.id.in_([asset.blob_id for asset in assets]))
                )
            )
            .scalars()
            .all()
        }

        by_frame: dict[int, list[ShapeRecord]] = {}
        tags_by_frame: dict[int, list[str]] = {}

        for job in jobs:
            await _collect_job_annotations(
                session, job, label_names, by_frame, tags_by_frame, present_types
            )

        for asset in assets:
            blob = blobs.get(asset.blob_id)
            for offset in range(asset.frame_count):
                frame_index = asset.start_frame + offset
                name = (
                    asset.name
                    if asset.frame_count == 1
                    else f"{asset.name.rsplit('.', 1)[0]}_{offset:06d}.jpg"
                )
                # Only single-frame assets carry their bytes into an export. Extracting
                # video frames is a background media job; shipping the container itself
                # under a frame's filename would be misleading.
                media: bytes | None = None
                if (
                    include_images
                    and storage is not None
                    and blob is not None
                    and asset.frame_count == 1
                ):
                    media = await media_service.read_blob(blob, storage)

                frame_records.append(
                    FrameRecord(
                        index=frame_index,
                        name=f"{task.name}/{name}" if len(tasks) > 1 else name,
                        width=blob.width if blob else None,
                        height=blob.height if blob else None,
                        shapes=by_frame.get(frame_index, []),
                        tags=tags_by_frame.get(frame_index, []),
                        media=media,
                    )
                )

    def frames() -> Iterator[FrameRecord]:
        yield from frame_records

    view = DatasetView(
        name=project.name,
        labels=[_label_spec(label) for label in labels if label.parent_id is None],
        frames=frames(),
        metadata={
            "project_id": str(project.id),
            "project_slug": project.slug,
            "exported_at": utcnow().isoformat(),
            "only_accepted": only_accepted,
            "generator": "CurveVision",
        },
    )
    return view, sorted(present_types, key=lambda shape: shape.value)


def _label_spec(label: Label) -> LabelSpec:
    children = sorted(label.children, key=lambda child: child.position)
    return LabelSpec(
        id=str(label.id),
        name=label.name,
        color=label.color,
        attributes=tuple(
            AttributeSpec(
                name=attribute.name,
                attribute_type=attribute.attribute_type,
                values=tuple(attribute.values),
                mutable=attribute.mutable,
            )
            for attribute in label.attributes
        ),
        keypoints=tuple(child.name for child in children),
        skeleton_edges=tuple(
            (int(edge[0]), int(edge[1])) for edge in label.skeleton_edges if len(edge) == 2
        ),
    )


async def _collect_job_annotations(
    session: AsyncSession,
    job: Job,
    label_names: dict[uuid.UUID, str],
    by_frame: dict[int, list[ShapeRecord]],
    tags_by_frame: dict[int, list[str]],
    present_types: set[ShapeType],
) -> None:
    shapes = (await session.execute(select(Shape).where(Shape.job_id == job.id))).scalars().all()
    for shape in shapes:
        present_types.add(ShapeType(shape.shape_type))
        by_frame.setdefault(shape.frame, []).append(
            ShapeRecord(
                label=label_names.get(shape.label_id, "unknown"),
                shape_type=ShapeType(shape.shape_type),
                points=list(shape.points),
                rotation=shape.rotation,
                occluded=shape.occluded,
                z_order=shape.z_order,
                group=shape.group,
                attributes=dict(shape.attributes),
                source=str(shape.source),
                confidence=shape.confidence,
                mask=shape.mask,
                elements=list(shape.elements),
            )
        )

    tracks = (
        (
            await session.execute(
                select(Track).where(Track.job_id == job.id).options(selectinload(Track.shapes))
            )
        )
        .scalars()
        .all()
    )
    for index, track in enumerate(tracks):
        present_types.add(ShapeType(track.shape_type))
        keyframes = [
            Keyframe(
                frame=ts.frame,
                points=list(ts.points),
                rotation=ts.rotation,
                occluded=ts.occluded,
                outside=ts.outside,
                keyframe=ts.keyframe,
                z_order=ts.z_order,
                attributes=dict(ts.attributes),
                mask=ts.mask,
                elements=list(ts.elements),
            )
            for ts in track.shapes
        ]
        if not keyframes:
            continue
        # Materialise the track across the job's frames: every format below this layer
        # sees plain per-frame shapes, so no format has to understand interpolation.
        for frame in range(job.start_frame, job.stop_frame + 1):
            position = interpolate_track(keyframes, ShapeType(track.shape_type), frame)
            if position is None:
                continue
            by_frame.setdefault(frame, []).append(
                ShapeRecord(
                    label=label_names.get(track.label_id, "unknown"),
                    shape_type=ShapeType(track.shape_type),
                    points=list(position.points),
                    rotation=position.rotation,
                    occluded=position.occluded,
                    z_order=position.z_order,
                    group=track.group,
                    track_id=track.object_id if track.object_id is not None else index,
                    attributes={**track.attributes, **position.attributes},
                    source=str(track.source),
                    confidence=track.confidence,
                    mask=position.mask,
                    elements=list(position.elements),
                )
            )

    tags = (await session.execute(select(Tag).where(Tag.job_id == job.id))).scalars().all()
    for tag in tags:
        name = label_names.get(tag.label_id, "unknown")
        frames = (
            [tag.frame]
            if tag.frame is not None
            else list(range(job.start_frame, job.stop_frame + 1))
        )
        for frame in frames:
            tags_by_frame.setdefault(frame, []).append(name)


async def export_dataset(
    session: AsyncSession,
    project: Project,
    *,
    format_id: str,
    task_ids: list[uuid.UUID] | None = None,
    only_accepted: bool = False,
    include_images: bool = False,
    storage: Storage | None = None,
) -> tuple[bytes, list[str]]:
    """Run an export. Returns the archive bytes and any capability warnings."""
    fmt = get_format(format_id)
    if not fmt.capabilities.supports_export:
        raise ValidationError(f"Format {format_id!r} does not support export")

    view, present = await build_dataset_view(
        session,
        project,
        task_ids=task_ids,
        only_accepted=only_accepted,
        include_images=include_images,
        storage=storage,
    )

    warnings = []
    dropped = fmt.capabilities.unsupported(present)
    if dropped:
        warnings.append(
            f"{fmt.name} cannot represent "
            f"{', '.join(shape.value for shape in dropped)}; those annotations were omitted."
        )

    with ZipExportSink() as sink:
        fmt.export(view, sink)
        if warnings:
            sink.write("curvevision_export_notes.txt", "\n".join(warnings) + "\n")
        archive = sink.close()
    return archive, warnings


# ------------------------------------------------------------------------------ import


async def build_import_context(
    session: AsyncSession, task: Task, *, create_missing_labels: bool
) -> ImportContext:
    labels = (
        (await session.execute(select(Label).where(Label.project_id == task.project_id)))
        .scalars()
        .all()
    )
    assets = (
        (
            await session.execute(
                select(Asset).where(Asset.task_id == task.id).order_by(Asset.position)
            )
        )
        .scalars()
        .all()
    )
    blobs = {
        blob.id: blob
        for blob in (
            await session.execute(
                select(MediaBlob).where(MediaBlob.id.in_([asset.blob_id for asset in assets]))
            )
        )
        .scalars()
        .all()
    }

    frame_by_name: dict[str, int] = {}
    frame_sizes: dict[int, tuple[int, int]] = {}
    for asset in assets:
        frame_by_name[asset.name] = asset.start_frame
        blob = blobs.get(asset.blob_id)
        if blob and blob.width and blob.height:
            for offset in range(asset.frame_count):
                frame_sizes[asset.start_frame + offset] = (blob.width, blob.height)

    return ImportContext(
        known_labels={label.name: str(label.id) for label in labels},
        frame_by_name=frame_by_name,
        frame_count=task.frame_count,
        frame_sizes=frame_sizes,
        create_missing_labels=create_missing_labels,
    )


def summarise_import(result: ImportResult) -> dict[str, Any]:
    return {
        "shapes": len(result.shapes),
        "tags": len(result.tags),
        "new_labels": [label.name for label in result.new_labels],
        "frames_matched": result.frames_matched,
        "warnings": result.warnings,
    }


# ---------------------------------------------------------------------- dataset versions


async def create_dataset_version(
    session: AsyncSession,
    project: Project,
    *,
    name: str,
    description: str | None,
    task_ids: list[uuid.UUID],
    created_by_id: uuid.UUID | None,
) -> DatasetVersion:
    existing = await session.execute(
        select(DatasetVersion).where(
            DatasetVersion.project_id == project.id, DatasetVersion.name == name
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"A dataset version named {name!r} already exists")

    version = DatasetVersion(
        project_id=project.id,
        name=name,
        description=description,
        state=DatasetVersionState.DRAFT,
        manifest={"task_ids": [str(task_id) for task_id in task_ids]},
        created_by_id=created_by_id,
    )
    session.add(version)
    await session.flush()
    return version


async def release_dataset_version(
    session: AsyncSession, project: Project, version: DatasetVersion
) -> DatasetVersion:
    """Freeze a version: compute its content hash and statistics, then seal it.

    A released version is immutable. Two releases with the same ``content_hash`` contain
    exactly the same annotations, which is what makes an experiment reproducible.
    """
    if version.state is DatasetVersionState.RELEASED:
        raise ConflictError("This dataset version is already released")

    task_ids = [uuid.UUID(value) for value in version.manifest.get("task_ids", [])]
    view, present = await build_dataset_view(
        session, project, task_ids=task_ids or None, only_accepted=False
    )

    hasher = hashlib.sha256()
    frame_count = 0
    empty_frames = 0
    shape_count = 0
    label_distribution: dict[str, int] = {}
    source_distribution: dict[str, int] = {}

    for frame in view:
        frame_count += 1
        if not frame.shapes and not frame.tags:
            empty_frames += 1
        hasher.update(f"{frame.index}:{frame.name}".encode())
        for shape in frame.shapes:
            shape_count += 1
            label_distribution[shape.label] = label_distribution.get(shape.label, 0) + 1
            source_distribution[shape.source] = source_distribution.get(shape.source, 0) + 1
            hasher.update(
                json.dumps(
                    [shape.label, shape.shape_type.value, shape.points], sort_keys=True
                ).encode()
            )

    version.content_hash = hasher.hexdigest()
    version.statistics = {
        "frame_count": frame_count,
        "shape_count": shape_count,
        "label_distribution": label_distribution,
        "source_distribution": source_distribution,
        "shape_types": [shape.value for shape in present],
        # An unannotated frame is usually a mistake, and is the single most useful number
        # to surface before anyone trains on the release.
        "frames_without_annotations": empty_frames,
    }
    version.manifest = {
        **version.manifest,
        "labels": [label.name for label in view.labels],
        "sealed_at": utcnow().isoformat(),
    }
    version.state = DatasetVersionState.RELEASED
    version.released_at = utcnow()
    await session.flush()
    return version


async def get_dataset_version(
    session: AsyncSession, project_id: uuid.UUID, version_id: uuid.UUID
) -> DatasetVersion:
    version = await session.get(DatasetVersion, version_id)
    if version is None or version.project_id != project_id:
        raise NotFoundError("Dataset version not found")
    return version


async def iter_dataset_frames(view: DatasetView) -> AsyncIterator[FrameRecord]:
    """Adapter for callers that want to consume the view asynchronously."""
    for frame in view:
        yield frame


__all__ = [
    "AnnotationSource",
    "build_dataset_view",
    "build_import_context",
    "create_dataset_version",
    "export_dataset",
    "get_dataset_version",
    "release_dataset_version",
    "summarise_import",
]
