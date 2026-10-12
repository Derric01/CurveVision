"""Dataset export, import and immutable versions.

Export builds a ``DatasetView`` whose ``frames`` is a **generator**, so a 500k-image
project is never materialised in memory. Tracks are flattened into per-frame shapes here,
before the format sees them, so every format gets one uniform shape stream.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from curvevision.core.db import utcnow
from curvevision.core.errors import ConflictError, NotFoundError, ValidationError
from curvevision.domain.annotation import Shape, Tag, Track, TrackShape
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
from curvevision.services import annotations as annotation_service
from curvevision.services import media as media_service
from curvevision.services import merge
from curvevision.services import projects as project_service
from curvevision.services import skeletons as skeleton_service
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

        # Jobs are folded in one at a time rather than concatenated, because a task with
        # `overlap > 0` hands the same frames to two annotators on purpose and their work on
        # the seam describes the same objects twice. See `services/merge.py`.
        covered: list[tuple[int, int]] = []
        track_ids = _TrackIds(await _reserved_object_ids(session, [job.id for job in jobs]))
        for job in jobs:
            job_frames: dict[int, list[ShapeRecord]] = {}
            job_tags: dict[int, list[str]] = {}
            await _collect_job_annotations(
                session, job, label_names, job_frames, job_tags, present_types, track_ids
            )
            shared = merge.overlapping_frames(covered, (job.start_frame, job.stop_frame))
            merge.merge_frames(by_frame, job_frames, shared_frames=shared)
            merge.merge_tags(tags_by_frame, job_tags, shared_frames=shared)
            covered.append((job.start_frame, job.stop_frame))

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


def _named(
    elements: list[dict[str, Any]], label_names: dict[uuid.UUID, str]
) -> list[dict[str, Any]]:
    """Each joint with its name, which is what a format carries: a label id names a label in
    one project only, and the project an archive is imported into is usually another."""
    named = []
    for element in elements:
        try:
            name = label_names.get(uuid.UUID(str(element.get("label_id"))))
        except ValueError:
            name = None
        named.append({**element, "name": name} if name else dict(element))
    return named


class _TrackIds:
    """Export-scoped track identity, unique across a whole task.

    Numbering each job's tracks from zero is the obvious thing and it is wrong: job 1's
    first car and job 2's first pedestrian both become `track_id: 0`, so an export claims
    one object teleported across the task and any consumer that groups by track id welds two
    objects together. Identity has to be allocated per task, not per job.

    A track that carries an explicit `object_id` keeps it — that field is documented as
    stable identity across the dataset, and an author who set it means it. Generated ids
    step around those values so the two kinds can never collide.
    """

    def __init__(self, reserved: set[int]) -> None:
        self._reserved = reserved
        self._assigned: dict[uuid.UUID, int] = {}
        self._next = 0

    def of(self, track: Track) -> int:
        if track.object_id is not None:
            return track.object_id
        existing = self._assigned.get(track.id)
        if existing is not None:
            return existing
        while self._next in self._reserved:
            self._next += 1
        value = self._next
        self._next += 1
        self._assigned[track.id] = value
        return value


async def _reserved_object_ids(session: AsyncSession, job_ids: list[uuid.UUID]) -> set[int]:
    """Every explicitly-set `object_id` in a task, so generated ids can avoid them."""
    if not job_ids:
        return set()
    result = await session.execute(
        select(Track.object_id).where(Track.job_id.in_(job_ids), Track.object_id.is_not(None))
    )
    return {value for value in result.scalars().all() if value is not None}


async def _collect_job_annotations(
    session: AsyncSession,
    job: Job,
    label_names: dict[uuid.UUID, str],
    by_frame: dict[int, list[ShapeRecord]],
    tags_by_frame: dict[int, list[str]],
    present_types: set[ShapeType],
    track_ids: _TrackIds,
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
                elements=_named(list(shape.elements), label_names),
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
    for track in tracks:
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
                elements=_named(list(ts.elements), label_names),
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
                    track_id=track_ids.of(track),
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


@dataclass(slots=True)
class ImportWritten:
    """What an import stored, and what it said about what it did not."""

    shapes: int = 0
    tracks: int = 0
    tags: int = 0
    warnings: list[str] = field(default_factory=list)


async def write_import(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    jobs: list[Job],
    result: ImportResult,
    label_ids: dict[str, str],
    actor_id: uuid.UUID,
) -> ImportWritten:
    """Store what a format read, as shapes, tracks and tags in the task's jobs.

    **Attributes are kept** where the label declares them and accepts the value, and the
    rest are dropped and named: storing one the label would refuse makes the annotation's
    next save fail, since every save validates its attributes. CVAT's importer keeps only
    the attributes a label declares too, unless `soft_attribute_import` is switched on.

    **Records sharing a `track_id` are one track again.** Formats see a track as one record
    per frame it is on (`_collect_job_annotations`), so each listed frame becomes a
    keyframe, and an `outside` keyframe goes wherever the track stops being listed — between
    two runs, and after the last, since the server holds a track's last position to the end
    of the job otherwise. A track is split where it crosses into another job, and one listed
    on a single frame is stored as a shape.

    Both were thrown away until iteration 62: every imported shape had no attributes, and a
    track came back as one unrelated box per frame.
    """
    schema = await annotation_service.LabelSchema.load(session, project_id)
    joints = await skeleton_service.skeleton_joints(session, project_id)
    written = ImportWritten()
    unmatched_labels: set[str] = set()
    jointless: set[str] = set()
    unknown_joints: set[str] = set()
    undeclared: set[str] = set()
    refused: set[str] = set()

    def kept(label_id: uuid.UUID, label: str, values: dict[str, Any]) -> dict[str, Any]:
        definitions = {definition.name: definition for definition in schema.definitions(label_id)}
        cleaned: dict[str, Any] = {}
        for name, value in values.items():
            definition = definitions.get(name)
            if definition is None:
                undeclared.add(f"{label}.{name}")
                continue
            try:
                cleaned.update(project_service.validate_attributes({name: value}, [definition]))
            except ValidationError:
                refused.add(f"{label}.{name}={value!r}")
        return cleaned

    def resolved(record: ShapeRecord, label_id: str) -> list[dict[str, Any]] | None:
        """The record's joints as this project stores them; None when it has none to use."""
        if record.shape_type is not ShapeType.SKELETON:
            return record.elements
        if label_id not in joints:
            jointless.add(record.label)
            return None
        elements, unknown = skeleton_service.resolve_joints(record.elements, joints[label_id])
        unknown_joints.update(unknown)
        return elements

    tracked: dict[tuple[int, str, ShapeType], list[tuple[int, ShapeRecord]]] = {}
    loose: list[tuple[int, ShapeRecord]] = []
    for frame, record in result.shapes:
        if record.track_id is None:
            loose.append((frame, record))
        else:
            key = (record.track_id, record.label, record.shape_type)
            tracked.setdefault(key, []).append((frame, record))
    # A track listed on one frame is a shape: it is how a tracked CVAT dump writes a shape
    # that was never tracked (a one-frame track, closed by `outside`), and over one frame
    # the two are the same object.
    for key, records in list(tracked.items()):
        if len({frame for frame, _record in records}) == 1:
            loose.append(records[0])
            del tracked[key]

    for frame, record in loose:
        target = _job_for_frame(jobs, frame)
        label_id = label_ids.get(record.label)
        if target is None:
            continue
        if label_id is None:
            unmatched_labels.add(record.label)
            continue
        elements = resolved(record, label_id)
        if elements is None:
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
                attributes=kept(uuid.UUID(label_id), record.label, record.attributes),
                mask=record.mask,
                elements=elements,
                created_by_id=actor_id,
            )
        )
        written.shapes += 1

    for (_track, label, shape_type), records in tracked.items():
        label_id = label_ids.get(label)
        if label_id is None:
            unmatched_labels.add(label)
            continue
        for job, run in _by_job(jobs, sorted(records, key=lambda item: item[0])):
            if _write_track(
                session,
                job=job,
                label=label,
                label_id=label_id,
                shape_type=shape_type,
                run=run,
                mutable=schema.mutable_attribute_names(uuid.UUID(label_id)),
                kept=kept,
                resolved=resolved,
                actor_id=actor_id,
            ):
                written.tracks += 1

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
                created_by_id=actor_id,
            )
        )
        written.tags += 1

    await session.flush()
    for job in jobs:
        job.annotation_version += 1
        await annotation_service.refresh_counts(session, job)

    for message, names in (
        ("Skipped annotations for labels not present in the project", unmatched_labels),
        ("Skipped skeletons of labels that declare no joints in this project", jointless),
        ("Dropped joints the project's skeleton labels do not declare", unknown_joints),
        ("Dropped attributes the project's labels do not declare", undeclared),
        ("Dropped attribute values the project's labels would refuse", refused),
    ):
        if names:
            written.warnings.append(f"{message}: {', '.join(sorted(names))}")
    return written


def _job_for_frame(jobs: list[Job], frame: int) -> Job | None:
    for job in jobs:
        if job.contains_frame(frame):
            return job
    return None


def _by_job(
    jobs: list[Job], records: list[tuple[int, ShapeRecord]]
) -> list[tuple[Job, list[tuple[int, ShapeRecord]]]]:
    """A track's records, split into a run per job they fall in, in frame order."""
    runs: list[tuple[Job, list[tuple[int, ShapeRecord]]]] = []
    seen: set[int] = set()
    for frame, record in records:
        if frame in seen:
            continue  # One position per frame; a file listing two keeps the first.
        seen.add(frame)
        job = _job_for_frame(jobs, frame)
        if job is None:
            continue
        if runs and runs[-1][0] is job:
            runs[-1][1].append((frame, record))
        else:
            runs.append((job, [(frame, record)]))
    return runs


def _write_track(
    session: AsyncSession,
    *,
    job: Job,
    label: str,
    label_id: str,
    shape_type: ShapeType,
    run: list[tuple[int, ShapeRecord]],
    mutable: set[str],
    kept: Callable[[uuid.UUID, str, dict[str, Any]], dict[str, Any]],
    resolved: Callable[[ShapeRecord, str], list[dict[str, Any]] | None],
    actor_id: uuid.UUID,
) -> bool:
    """One track from one job's run of records; False when its records cannot be stored."""
    elements = [resolved(record, label_id) for _frame, record in run]
    if any(joints is None for joints in elements):
        return False
    label_uuid = uuid.UUID(label_id)
    first = run[0][1]
    track = Track(
        id=uuid.uuid4(),
        job_id=job.id,
        label_id=label_uuid,
        shape_type=shape_type,
        group=first.group,
        source=AnnotationSource.IMPORTED,
        # What does not vary per frame lives on the track, as the editor writes it.
        attributes={
            k: v for k, v in kept(label_uuid, label, first.attributes).items() if k not in mutable
        },
        created_by_id=actor_id,
    )
    session.add(track)
    for index, ((frame, record), joints) in enumerate(zip(run, elements, strict=True)):
        following = run[index + 1][0] if index + 1 < len(run) else None
        session.add(
            TrackShape(
                track_id=track.id,
                frame=frame,
                points=record.points,
                rotation=record.rotation,
                occluded=record.occluded,
                outside=False,
                keyframe=True,
                z_order=record.z_order,
                attributes={
                    k: v
                    for k, v in kept(label_uuid, label, record.attributes).items()
                    if k in mutable
                },
                mask=record.mask,
                elements=joints or [],
            )
        )
        # Not listed on the next frame: it left. Without this the server would interpolate
        # across the gap, or hold the last position to the end of the job.
        leaves = frame + 1
        if (following is None or following > leaves) and leaves <= job.stop_frame:
            session.add(
                TrackShape(
                    track_id=track.id,
                    frame=leaves,
                    points=record.points,
                    rotation=record.rotation,
                    occluded=False,
                    outside=True,
                    keyframe=True,
                    z_order=record.z_order,
                    attributes={},
                    mask=record.mask,
                    elements=joints or [],
                )
            )
    return True


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
    "ImportWritten",
    "build_dataset_view",
    "build_import_context",
    "create_dataset_version",
    "export_dataset",
    "get_dataset_version",
    "release_dataset_version",
    "summarise_import",
    "write_import",
]
