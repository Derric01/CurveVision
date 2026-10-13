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
from dataclasses import dataclass, field, replace
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
    MemoryExportSink,
    ShapeRecord,
    TagRecord,
    ZipExportSink,
    get_format,
)
from curvevision.formats import native as native_format
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
        tags_by_frame: dict[int, list[TagRecord]] = {}

        # Jobs are folded in one at a time rather than concatenated, because a task with
        # `overlap > 0` hands the same frames to two annotators on purpose and their work on
        # the seam describes the same objects twice. See `services/merge.py`.
        covered: list[tuple[int, int]] = []
        track_ids = _TrackIds(await _reserved_object_ids(session, [job.id for job in jobs]))
        for job in jobs:
            job_frames: dict[int, list[ShapeRecord]] = {}
            job_tags: dict[int, list[TagRecord]] = {}
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
                        tags=[tag.label for tag in tags_by_frame.get(frame_index, [])],
                        tag_records=tags_by_frame.get(frame_index, []),
                        media=media,
                        media_sha256=blob.sha256 if blob else None,
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
    tags_by_frame: dict[int, list[TagRecord]],
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
                    keyframe=position.keyframe,
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
            tags_by_frame.setdefault(frame, []).append(
                TagRecord(
                    label=name,
                    attributes=dict(tag.attributes),
                    source=str(tag.source),
                    confidence=tag.confidence,
                )
            )


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
    return _run_export(format_id, view, present, [])


def _run_export(
    format_id: str, view: DatasetView, present: list[ShapeType], notes: list[str]
) -> tuple[bytes, list[str]]:
    fmt = get_format(format_id)
    warnings = list(notes)
    dropped = fmt.capabilities.unsupported(present)
    if dropped:
        warnings.insert(
            0,
            f"{fmt.name} cannot represent "
            f"{', '.join(shape.value for shape in dropped)}; those annotations were omitted.",
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

    **Records sharing a `track_id` are one track again**, each listed frame a keyframe. What
    a frame the track does not list means is the format's to say (`ImportResult.track_gaps`):

    * ``"absent"`` — a per-frame format (MOTChallenge, any per-frame export, which is how
      `_collect_job_annotations` hands a track to a format): an `outside` keyframe goes
      wherever the track stops being listed, between two runs and after the last, since the
      server holds a track's last position to the end of the job otherwise.
    * ``"interpolated"`` — CVAT XML: the object moves between the frames listed and leaves
      only where a record says ``outside``, as CVAT's own reader has it.

    A track is split where it crosses into another job, as CVAT's `_slice_track` splits
    one: each part gets the interpolated shape on its first frame when the object is
    already there, and on its last when it carries on. One on a single frame is a shape.

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

    def provenance(source: str, confidence: float | None) -> tuple[AnnotationSource, float | None]:
        """The record's own provenance where the format carries it; `imported` otherwise.

        A pending model suggestion is `model` with a confidence (accepting one clears it),
        so restoring a backup as `imported` turned every pending suggestion into finished
        work. `interpolated` describes an export's frame, never a stored annotation.
        """
        if not result.carries_provenance:
            return AnnotationSource.IMPORTED, None
        try:
            kind = AnnotationSource(source)
        except ValueError:
            return AnnotationSource.IMPORTED, None
        if kind is AnnotationSource.INTERPOLATED:
            return AnnotationSource.IMPORTED, None
        if isinstance(confidence, int | float) and 0.0 <= float(confidence) <= 1.0:
            return kind, float(confidence)
        return kind, None

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

    gaps_absent = result.track_gaps == "absent"
    last_frame = max((job.stop_frame for job in jobs), default=-1)
    tracked: dict[tuple[int, str, ShapeType], list[tuple[int, ShapeRecord]]] = {}
    #: Where each track leaves, by id and label: an `outside` record's geometry is a stale
    #: copy, and need not even read as the track's shape type.
    leaving: dict[tuple[int, str], set[int]] = {}
    loose: list[tuple[int, ShapeRecord]] = []
    for frame, record in result.shapes:
        if record.track_id is None:
            if not record.outside:
                loose.append((frame, record))
        elif record.outside:
            leaving.setdefault((record.track_id, record.label), set()).add(frame)
        else:
            key = (record.track_id, record.label, record.shape_type)
            tracked.setdefault(key, []).append((frame, record))
    # A track on one frame is a shape: it is how a tracked CVAT dump writes a shape that was
    # never tracked (a one-frame track, closed by `outside`), and over one frame the two are
    # the same object. Where gaps are interpolated, a lone keyframe with nothing closing it
    # is an object that stays to the end — a track — unless it is on the last frame anyway.
    for key, records in list(tracked.items()):
        frames = {frame for frame, _record in records}
        if len(frames) != 1:
            continue
        [only] = frames
        if gaps_absent or only == last_frame or only + 1 in leaving.get(key[:2], set()):
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
        source, confidence = provenance(record.source, record.confidence)
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
                source=source,
                confidence=confidence,
                attributes=kept(uuid.UUID(label_id), record.label, record.attributes),
                mask=record.mask,
                elements=elements,
                created_by_id=actor_id,
            )
        )
        written.shapes += 1

    for (track_id, label, shape_type), records in tracked.items():
        label_id = label_ids.get(label)
        if label_id is None:
            unmatched_labels.add(label)
            continue
        label_uuid = uuid.UUID(label_id)

        def attributes_of(
            record: ShapeRecord, label_uuid: uuid.UUID = label_uuid, label: str = label
        ) -> dict[str, Any]:
            return kept(label_uuid, label, record.attributes)

        def joints_of(record: ShapeRecord, label_id: str = label_id) -> list[dict[str, Any]] | None:
            return resolved(record, label_id)

        keyframes = _keyframes_of(
            shape_type,
            records,
            leaving.get((track_id, label), set()),
            gaps_absent=gaps_absent,
            last_frame=last_frame,
            attributes_of=attributes_of,
            joints_of=joints_of,
        )
        if keyframes is None:
            continue
        first_record = min(records, key=lambda item: item[0])[1]
        source, confidence = provenance(first_record.source, first_record.confidence)
        for job, part in _by_job(jobs, keyframes, shape_type):
            _write_track(
                session,
                job=job,
                label_id=label_uuid,
                shape_type=shape_type,
                group=first_record.group,
                source=source,
                confidence=confidence,
                keyframes=part,
                mutable=schema.mutable_attribute_names(label_uuid),
                actor_id=actor_id,
            )
            written.tracks += 1

    for frame, tag in result.tags:
        full = tag if isinstance(tag, TagRecord) else TagRecord(label=tag, source="imported")
        target = _job_for_frame(jobs, frame)
        label_id = label_ids.get(full.label)
        if target is None or label_id is None:
            continue
        source, confidence = provenance(full.source, full.confidence)
        session.add(
            Tag(
                job_id=target.id,
                label_id=uuid.UUID(label_id),
                frame=frame,
                source=source,
                confidence=confidence,
                attributes=kept(uuid.UUID(label_id), full.label, full.attributes),
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


def _keyframes_of(
    shape_type: ShapeType,
    records: list[tuple[int, ShapeRecord]],
    leaving: set[int],
    *,
    gaps_absent: bool,
    last_frame: int,
    attributes_of: Callable[[ShapeRecord], dict[str, Any]],
    joints_of: Callable[[ShapeRecord], list[dict[str, Any]] | None],
) -> list[Keyframe] | None:
    """One track's keyframes, in frame order, departures included; None if unstorable.

    A departure (`outside`) carries the geometry of the keyframe before it, as the editor
    writes one: its own is a stale copy, and need not even read as the track's shape type.
    One before the track's first appearance says nothing and is dropped, as CVAT drops it.

    A frame the file marks as not a keyframe (`ShapeRecord.keyframe`) counts as a frame the
    object is on, and is stored only where the keyframes around it do not already put it
    there (`_unreproduced`) — so a track comes back with the keyframes it was exported
    with, and never at a position other than the one the file gave.
    """
    present: dict[int, Keyframe] = {}
    for frame, record in sorted(records, key=lambda item: item[0]):
        if frame in present:
            continue  # One position per frame; a file listing two keeps the first.
        joints = joints_of(record)
        if joints is None:
            return None
        present[frame] = Keyframe(
            frame=frame,
            points=record.points,
            rotation=record.rotation,
            occluded=record.occluded,
            z_order=record.z_order,
            attributes=attributes_of(record),
            mask=record.mask,
            elements=joints or [],
            keyframe=record.keyframe,
        )
    frames = sorted(present)
    if gaps_absent:
        # Not listed on the next frame: it left.
        departures = {
            frame + 1
            for frame, following in zip(frames, [*frames[1:], None], strict=True)
            if (following is None or following > frame + 1) and frame + 1 <= last_frame
        }
    else:
        departures = {frame for frame in leaving if frame not in present}

    keyframes: list[Keyframe] = []
    previous: Keyframe | None = None
    for frame in sorted({*frames, *departures}):
        if frame in present:
            previous = present[frame]
            keyframes.append(previous)
        elif previous is not None and not keyframes[-1].outside:
            keyframes.append(
                replace(
                    previous,
                    frame=frame,
                    occluded=False,
                    outside=True,
                    keyframe=True,
                    attributes={},
                )
            )
    return _unreproduced(keyframes, shape_type)


#: How far a frame that was not a keyframe may sit from where interpolation puts it and
#: still be dropped as reproduced: CVAT XML writes coordinates to two decimal places.
_REPRODUCED_WITHIN = 0.02


def _unreproduced(keyframes: list[Keyframe], shape_type: ShapeType) -> list[Keyframe]:
    """The keyframes, less every frame not marked one that interpolation reproduces.

    A frame marked as interpolated is not trusted to be: it is dropped only where the
    keyframes kept around it put the object at the same place, in the same state, and kept
    — as a keyframe — wherever they do not (a tool that interpolates differently, a run's
    first frame). Keeping one changes what the frames on either side interpolate to, so
    the pass repeats until nothing more is kept.
    """
    kept = [kf for kf in keyframes if kf.keyframe or kf.outside]
    pending = [kf for kf in keyframes if not kf.keyframe and not kf.outside]
    changed = True
    while changed and pending:
        changed = False
        for candidate in list(pending):
            predicted = interpolate_track(kept, shape_type, candidate.frame) if kept else None
            if predicted is not None and _same_place(predicted, candidate):
                continue
            pending.remove(candidate)
            kept.append(replace(candidate, keyframe=True))
            kept.sort(key=lambda kf: kf.frame)
            changed = True
    return kept


def _same_place(a: Keyframe, b: Keyframe) -> bool:
    def close(left: list[float], right: list[float]) -> bool:
        return len(left) == len(right) and all(
            abs(x - y) <= _REPRODUCED_WITHIN for x, y in zip(left, right, strict=True)
        )

    turn = abs(a.rotation - b.rotation) % 360.0
    return (
        close(a.points, b.points)
        and min(turn, 360.0 - turn) <= _REPRODUCED_WITHIN
        and a.occluded == b.occluded
        and a.z_order == b.z_order
        and a.attributes == b.attributes
        and a.mask == b.mask
        and len(a.elements) == len(b.elements)
        and all(
            close(list(x.get("points") or []), list(y.get("points") or []))
            and bool(x.get("occluded")) == bool(y.get("occluded"))
            and bool(x.get("outside")) == bool(y.get("outside"))
            for x, y in zip(a.elements, b.elements, strict=True)
        )
    )


def _by_job(
    jobs: list[Job], keyframes: list[Keyframe], shape_type: ShapeType
) -> list[tuple[Job, list[Keyframe]]]:
    """A track's keyframes, split into the part each job holds, as CVAT's `_slice_track`.

    A job holds the keyframes on its frames, less any leading departure, which says nothing
    there. Where the object is already present on a job's first frame, that frame gets its
    interpolated shape as a keyframe; where the object carries on past the job's last
    frame, so does that one — without them, the part in the later job would start late and
    the one in the earlier job would stop moving at its last keyframe instead of at its
    boundary. A part with nothing present on it is no track.
    """
    parts: list[tuple[Job, list[Keyframe]]] = []
    for job in jobs:
        start, stop = job.start_frame, job.stop_frame
        part = [kf for kf in keyframes if start <= kf.frame <= stop]
        if not part or part[0].frame != start:
            entering = interpolate_track(keyframes, shape_type, start)
            if entering is not None:
                part.insert(0, replace(entering, frame=start, keyframe=True))
        # Only now: a departure after the shape it enters with is not a leading one.
        while part and part[0].outside:
            part.pop(0)
        following = next((kf for kf in keyframes if kf.frame > stop), None)
        if part and part[-1].frame != stop and following is not None and not following.outside:
            through = interpolate_track(keyframes, shape_type, stop)
            if through is not None:
                part.append(replace(through, frame=stop, keyframe=True))
        if any(not kf.outside for kf in part):
            parts.append((job, part))
    return parts


def _write_track(
    session: AsyncSession,
    *,
    job: Job,
    label_id: uuid.UUID,
    shape_type: ShapeType,
    group: int | None,
    source: AnnotationSource,
    confidence: float | None,
    keyframes: list[Keyframe],
    mutable: set[str],
    actor_id: uuid.UUID,
) -> None:
    """One job's part of a track, as rows."""
    first = next(kf for kf in keyframes if not kf.outside)
    track = Track(
        id=uuid.uuid4(),
        job_id=job.id,
        label_id=label_id,
        shape_type=shape_type,
        group=group,
        source=source,
        confidence=confidence,
        # What does not vary per frame lives on the track, as the editor writes it.
        attributes={k: v for k, v in first.attributes.items() if k not in mutable},
        created_by_id=actor_id,
    )
    session.add(track)
    for keyframe in keyframes:
        session.add(
            TrackShape(
                track_id=track.id,
                frame=keyframe.frame,
                points=keyframe.points,
                rotation=keyframe.rotation,
                occluded=keyframe.occluded,
                outside=keyframe.outside,
                keyframe=True,
                z_order=keyframe.z_order,
                attributes={k: v for k, v in keyframe.attributes.items() if k in mutable},
                mask=keyframe.mask,
                elements=keyframe.elements,
            )
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


def snapshot_key(version: DatasetVersion) -> str:
    """Where a released version's frozen annotations are stored."""
    return f"versions/{version.project_id}/{version.id}/dataset.json"


async def release_dataset_version(
    session: AsyncSession, project: Project, version: DatasetVersion, storage: Storage
) -> DatasetVersion:
    """Freeze a version: store its annotations, compute their hash and statistics, seal it.

    A released version is immutable. Two releases with the same ``content_hash`` contain
    exactly the same annotations, which is what makes an experiment reproducible.

    Until iteration 66 the hash and the statistics were all a release kept: exporting the
    version later read the project as it was *then*, so a release could not be reproduced
    once anyone edited an annotation. Now the annotations themselves are frozen, in the
    lossless native format, and `export_dataset_version` exports from that copy.
    """
    if version.state is DatasetVersionState.RELEASED:
        raise ConflictError("This dataset version is already released")

    task_ids = [uuid.UUID(value) for value in version.manifest.get("task_ids", [])]
    live, present = await build_dataset_view(
        session, project, task_ids=task_ids or None, only_accepted=False
    )
    # Read once, used twice: for the hash and statistics, and for the frozen copy.
    view = DatasetView(
        name=live.name, labels=live.labels, frames=list(live), metadata=live.metadata
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
    sink = MemoryExportSink()
    get_format("curvevision").export(view, sink)
    frozen = sink.files["dataset.json"]
    key = snapshot_key(version)
    await storage.put(key, frozen, content_type="application/json")

    version.manifest = {
        **version.manifest,
        "labels": [label.name for label in view.labels],
        "sealed_at": utcnow().isoformat(),
        "snapshot_key": key,
        "snapshot_sha256": hashlib.sha256(frozen).hexdigest(),
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


async def version_snapshot_keys(session: AsyncSession, project_id: uuid.UUID) -> list[str]:
    """Where a project's released versions keep their frozen annotations."""
    versions = (
        await session.execute(select(DatasetVersion).where(DatasetVersion.project_id == project_id))
    ).scalars()
    return [str(key) for version in versions if (key := version.manifest.get("snapshot_key"))]


async def export_dataset_version(
    session: AsyncSession,
    project: Project,
    version: DatasetVersion,
    *,
    format_id: str,
    include_images: bool,
    storage: Storage,
) -> tuple[bytes, list[str]]:
    """Export a dataset version in any format.

    A released version exports from its frozen copy, so it is the same annotations however
    the project has changed since. A draft has nothing frozen yet and exports the project's
    current annotations for the version's tasks — what releasing it now would freeze.

    Images are found by content hash, wherever they are still stored; one whose every task
    has since been deleted is gone, and the export says which frames lost theirs.
    """
    fmt = get_format(format_id)
    if not fmt.capabilities.supports_export:
        raise ValidationError(f"Format {format_id!r} does not support export")

    if version.state is not DatasetVersionState.RELEASED:
        task_ids = [uuid.UUID(value) for value in version.manifest.get("task_ids", [])]
        return await export_dataset(
            session,
            project,
            format_id=format_id,
            task_ids=task_ids or None,
            include_images=include_images,
            storage=storage if include_images else None,
        )

    key = version.manifest.get("snapshot_key")
    if not key:
        raise ConflictError(
            f"{version.name!r} was released before releases kept a frozen copy of their "
            "annotations (iteration 66), so there is no copy to export. Its content hash "
            "and statistics still describe it; release a new version to freeze one."
        )
    frozen = await storage.get(str(key))
    if hashlib.sha256(frozen).hexdigest() != version.manifest.get("snapshot_sha256"):
        raise ConflictError(
            f"The frozen copy of {version.name!r} does not match the hash recorded when it "
            "was released; it has been altered or damaged in storage."
        )
    view = native_format.read_view(json.loads(frozen))
    frames = list(view)
    present = sorted(
        {shape.shape_type for frame in frames for shape in frame.shapes},
        key=lambda shape: shape.value,
    )

    notes: list[str] = []
    if include_images:
        missing = await _attach_media(session, frames, storage)
        if missing:
            shown = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
            notes.append(
                f"{len(missing)} frame(s) have no image: every task holding it was deleted "
                f"after this version was released ({shown})."
            )
    view = DatasetView(name=view.name, labels=view.labels, frames=frames, metadata=view.metadata)
    return _run_export(format_id, view, present, notes)


async def _attach_media(
    session: AsyncSession, frames: list[FrameRecord], storage: Storage
) -> list[str]:
    """Give each single-image frame its bytes, found by hash. Returns the frames without."""
    hashes = {frame.media_sha256 for frame in frames if frame.media_sha256}
    blobs = {
        blob.sha256: blob
        for blob in (await session.execute(select(MediaBlob).where(MediaBlob.sha256.in_(hashes))))
        .scalars()
        .all()
    }
    missing: list[str] = []
    for frame in frames:
        blob = blobs.get(frame.media_sha256 or "")
        # As in a live export, only single-image frames carry their bytes.
        if blob is None:
            missing.append(frame.name)
        elif (blob.frame_count or 1) == 1:
            frame.media = await media_service.read_blob(blob, storage)
    return missing


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
    "export_dataset_version",
    "get_dataset_version",
    "release_dataset_version",
    "summarise_import",
    "version_snapshot_keys",
    "write_import",
]
