"""The annotation engine's server side: reads, batched writes, and frame materialisation.

The write API is a single batch document per save. That choice drives everything here:

* One request per autosave, not one per object, so a 500-shape paste is one transaction.
* Optimistic concurrency on ``job.annotation_version``: the client sends the version it
  read, and a stale write is rejected rather than silently clobbering a co-annotator.
* One ``AnnotationEvent`` per batch, so history stays readable and bounded.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from curvevision.core.errors import (
    ConflictError,
    CurveVisionError,
    NotFoundError,
    ValidationError,
)
from curvevision.domain.annotation import Shape, Tag, Track, TrackShape
from curvevision.domain.enums import AnnotationSource, ShapeType
from curvevision.domain.identity import User
from curvevision.domain.project import AttributeDefinition, Label
from curvevision.domain.review import AnnotationEvent
from curvevision.domain.task import Job
from curvevision.schemas.annotation import (
    AnnotationWrite,
    ShapeIn,
    TagIn,
    TrackIn,
    TrackShapeIn,
)
from curvevision.services.interpolation import Keyframe, interpolate_track
from curvevision.services.projects import validate_attributes


class LabelSchema:
    """Label metadata for one project, loaded once per request.

    Validating every shape against the database individually is the obvious implementation
    and the wrong one -- a 5,000-shape import would issue 5,000 queries.
    """

    def __init__(self, labels: list[Label]) -> None:
        self._labels = {label.id: label for label in labels}
        self._attributes: dict[uuid.UUID, list[AttributeDefinition]] = {
            label.id: list(label.attributes) for label in labels
        }

    @classmethod
    async def load(cls, session: AsyncSession, project_id: uuid.UUID) -> LabelSchema:
        result = await session.execute(
            select(Label)
            .where(Label.project_id == project_id)
            .options(selectinload(Label.attributes))
        )
        return cls(list(result.scalars().all()))

    def label(self, label_id: uuid.UUID) -> Label:
        try:
            return self._labels[label_id]
        except KeyError as exc:
            raise ValidationError(f"Label {label_id} does not belong to this project") from exc

    def check_shape_type(self, label_id: uuid.UUID, shape_type: ShapeType) -> None:
        allowed = self.label(label_id).allowed_shape_types
        if allowed and shape_type.value not in allowed:
            raise ValidationError(
                f"Label {self.label(label_id).name!r} does not permit {shape_type.value} shapes"
            )

    def clean_attributes(self, label_id: uuid.UUID, values: dict[str, Any]) -> dict[str, Any]:
        return validate_attributes(values, self._attributes.get(label_id, []))

    def mutable_attribute_names(self, label_id: uuid.UUID) -> set[str]:
        return {
            attribute.name for attribute in self._attributes.get(label_id, []) if attribute.mutable
        }


# ------------------------------------------------------------------------------ reads


async def load_annotations(
    session: AsyncSession,
    job: Job,
    *,
    start_frame: int | None = None,
    stop_frame: int | None = None,
) -> dict[str, Any]:
    """Load a job's annotations, optionally restricted to a frame window."""
    shape_stmt = select(Shape).where(Shape.job_id == job.id)
    tag_stmt = select(Tag).where(Tag.job_id == job.id)
    if start_frame is not None:
        shape_stmt = shape_stmt.where(Shape.frame >= start_frame)
        tag_stmt = tag_stmt.where((Tag.frame >= start_frame) | (Tag.frame.is_(None)))
    if stop_frame is not None:
        shape_stmt = shape_stmt.where(Shape.frame <= stop_frame)
        tag_stmt = tag_stmt.where((Tag.frame <= stop_frame) | (Tag.frame.is_(None)))

    shapes = list((await session.execute(shape_stmt.order_by(Shape.frame))).scalars().all())
    tags = list((await session.execute(tag_stmt)).scalars().all())
    tracks = list(
        (
            await session.execute(
                select(Track).where(Track.job_id == job.id).options(selectinload(Track.shapes))
            )
        )
        .scalars()
        .all()
    )

    return {
        "job_id": job.id,
        "annotation_version": job.annotation_version,
        "shapes": shapes,
        "tracks": tracks,
        "tags": tags,
    }


async def frame_annotations(session: AsyncSession, job: Job, frame: int) -> dict[str, Any]:
    """Everything visible on one frame, with tracks materialised into shapes.

    This is what the editor asks for when it seeks: interpolation happens server-side so
    that a scripted client and the browser see exactly the same geometry.
    """
    if not job.contains_frame(frame):
        raise ValidationError(
            f"Frame {frame} is outside this job ({job.start_frame}-{job.stop_frame})"
        )

    shapes = list(
        (await session.execute(select(Shape).where(Shape.job_id == job.id, Shape.frame == frame)))
        .scalars()
        .all()
    )
    tags = list(
        (
            await session.execute(
                select(Tag).where(
                    Tag.job_id == job.id, (Tag.frame == frame) | (Tag.frame.is_(None))
                )
            )
        )
        .scalars()
        .all()
    )
    tracks = list(
        (
            await session.execute(
                select(Track).where(Track.job_id == job.id).options(selectinload(Track.shapes))
            )
        )
        .scalars()
        .all()
    )

    materialised: list[dict[str, Any]] = [_shape_to_dict(shape) for shape in shapes]
    for track in tracks:
        keyframes = [track_shape_to_keyframe(ts) for ts in track.shapes]
        position = interpolate_track(keyframes, ShapeType(track.shape_type), frame)
        if position is None:
            continue
        materialised.append(
            {
                "id": track.id,
                "client_id": track.client_id,
                "label_id": track.label_id,
                "frame": frame,
                "shape_type": track.shape_type,
                "points": position.points,
                "rotation": position.rotation,
                "occluded": position.occluded,
                "outside": False,
                "z_order": position.z_order,
                "group": track.group,
                "source": (track.source if position.keyframe else AnnotationSource.INTERPOLATED),
                "confidence": track.confidence,
                "attributes": {**track.attributes, **position.attributes},
                "mask": position.mask,
                "elements": position.elements,
            }
        )

    return {"frame": frame, "shapes": materialised, "tags": tags}


def _shape_to_dict(shape: Shape) -> dict[str, Any]:
    return {
        "id": shape.id,
        "client_id": shape.client_id,
        "label_id": shape.label_id,
        "frame": shape.frame,
        "shape_type": shape.shape_type,
        "points": shape.points,
        "rotation": shape.rotation,
        "occluded": shape.occluded,
        "outside": shape.outside,
        "z_order": shape.z_order,
        "group": shape.group,
        "source": shape.source,
        "confidence": shape.confidence,
        "attributes": shape.attributes,
        "mask": shape.mask,
        "elements": shape.elements,
    }


def track_shape_to_keyframe(track_shape: TrackShape) -> Keyframe:
    """A stored keyframe as the interpolator wants it. Shared with `quality`."""
    return Keyframe(
        frame=track_shape.frame,
        points=list(track_shape.points),
        rotation=track_shape.rotation,
        occluded=track_shape.occluded,
        outside=track_shape.outside,
        keyframe=track_shape.keyframe,
        z_order=track_shape.z_order,
        attributes=dict(track_shape.attributes),
        mask=track_shape.mask,
        elements=list(track_shape.elements),
    )


# ----------------------------------------------------------------------------- writes


async def write_annotations(
    session: AsyncSession,
    job: Job,
    payload: AnnotationWrite,
    *,
    actor: User,
    schema: LabelSchema,
    action: str = "update",
) -> dict[str, Any]:
    """Apply one batch of annotation changes atomically."""
    if job.locked:
        raise ConflictError("This job is locked")
    if (
        payload.annotation_version is not None
        and payload.annotation_version != job.annotation_version
    ):
        raise ConflictError(
            "These annotations changed while you were editing. Reload the job before saving.",
            expected_version=job.annotation_version,
            submitted_version=payload.annotation_version,
        )

    created: dict[str, int] = defaultdict(int)
    updated: dict[str, int] = defaultdict(int)
    deleted: dict[str, int] = defaultdict(int)
    id_map: dict[str, uuid.UUID] = {}

    for index, incoming_shape in enumerate(payload.created_shapes):
        with _entry("created_shapes", index):
            shape = await _create_shape(session, job, incoming_shape, schema, actor)
        created["shapes"] += 1
        if incoming_shape.client_id:
            id_map[incoming_shape.client_id] = shape.id

    for index, incoming_shape in enumerate(payload.updated_shapes):
        with _entry("updated_shapes", index):
            await _update_shape(session, job, incoming_shape, schema)
        updated["shapes"] += 1

    if payload.deleted_shapes:
        deleted["shapes"] = await _delete_rows(session, Shape, job.id, payload.deleted_shapes)

    for index, incoming_track in enumerate(payload.created_tracks):
        with _entry("created_tracks", index):
            track = await _create_track(session, job, incoming_track, schema, actor)
        created["tracks"] += 1
        if incoming_track.client_id:
            id_map[incoming_track.client_id] = track.id

    for index, incoming_track in enumerate(payload.updated_tracks):
        with _entry("updated_tracks", index):
            await _update_track(session, job, incoming_track, schema)
        updated["tracks"] += 1

    if payload.deleted_tracks:
        deleted["tracks"] = await _delete_rows(session, Track, job.id, payload.deleted_tracks)

    for index, incoming_tag in enumerate(payload.created_tags):
        with _entry("created_tags", index):
            tag = await _create_tag(session, job, incoming_tag, schema, actor)
        created["tags"] += 1
        if incoming_tag.client_id:
            id_map[incoming_tag.client_id] = tag.id

    for index, incoming_tag in enumerate(payload.updated_tags):
        with _entry("updated_tags", index):
            await _update_tag(session, job, incoming_tag, schema)
        updated["tags"] += 1

    if payload.deleted_tags:
        deleted["tags"] = await _delete_rows(session, Tag, job.id, payload.deleted_tags)

    await session.flush()
    job.annotation_version += 1
    await refresh_counts(session, job)

    session.add(
        AnnotationEvent(
            job_id=job.id,
            actor_id=actor.id,
            action=action,
            annotation_version=job.annotation_version,
            summary={"created": dict(created), "updated": dict(updated), "deleted": dict(deleted)},
        )
    )
    await session.flush()

    return {
        "job_id": job.id,
        "annotation_version": job.annotation_version,
        "created": dict(created),
        "updated": dict(updated),
        "deleted": dict(deleted),
        "id_map": id_map,
    }


@contextmanager
def _entry(field: str, index: int) -> Iterator[None]:
    """Name the batch entry a refusal came from, as request validation names a field.

    The batch is still refused whole — it is one transaction. But a client that cannot tell
    which of its entries was refused can only resend all of them, and an autosave that did
    exactly that failed every save after one bad object, however many good ones followed.
    """
    try:
        yield
    except CurveVisionError as exc:
        exc.extra.setdefault(
            "errors",
            [{"location": ["body", field, index], "message": exc.detail, "type": exc.error_type}],
        )
        raise


async def _create_shape(
    session: AsyncSession, job: Job, incoming: ShapeIn, schema: LabelSchema, actor: User
) -> Shape:
    _check_frame(job, incoming.frame)
    schema.check_shape_type(incoming.label_id, incoming.shape_type)
    if incoming.client_id:
        existing = await session.scalar(
            select(Shape).where(Shape.job_id == job.id, Shape.client_id == incoming.client_id)
        )
        if existing is not None:
            # The job keeps one shape per client id. The same object again is a create
            # resent because its answer was lost, and is answered with the shape it made;
            # a different one is a client reusing an id, refused rather than dropped.
            if (
                existing.label_id == incoming.label_id
                and existing.frame == incoming.frame
                and existing.shape_type == incoming.shape_type
                and existing.points == incoming.points
            ):
                return existing
            raise ValidationError(
                f"Client id {incoming.client_id!r} already names shape {existing.id} in this "
                "job, which is a different object"
            )
    shape = Shape(
        job_id=job.id,
        client_id=incoming.client_id,
        label_id=incoming.label_id,
        frame=incoming.frame,
        shape_type=incoming.shape_type,
        points=incoming.points,
        rotation=incoming.rotation,
        occluded=incoming.occluded,
        outside=incoming.outside,
        z_order=incoming.z_order,
        group=incoming.group,
        source=incoming.source,
        confidence=incoming.confidence,
        attributes=schema.clean_attributes(incoming.label_id, incoming.attributes),
        mask=incoming.mask.model_dump() if incoming.mask else None,
        elements=[element.model_dump(mode="json") for element in incoming.elements],
        created_by_id=actor.id,
    )
    session.add(shape)
    await session.flush()
    return shape


async def _update_shape(
    session: AsyncSession, job: Job, incoming: ShapeIn, schema: LabelSchema
) -> Shape:
    shape = await _load_owned(session, Shape, job.id, incoming.id, "Shape")
    _check_frame(job, incoming.frame)
    schema.check_shape_type(incoming.label_id, incoming.shape_type)
    shape.label_id = incoming.label_id
    shape.frame = incoming.frame
    shape.shape_type = incoming.shape_type
    shape.points = incoming.points
    shape.rotation = incoming.rotation
    shape.occluded = incoming.occluded
    shape.outside = incoming.outside
    shape.z_order = incoming.z_order
    shape.group = incoming.group
    shape.attributes = schema.clean_attributes(incoming.label_id, incoming.attributes)
    shape.mask = incoming.mask.model_dump() if incoming.mask else None
    shape.elements = [element.model_dump(mode="json") for element in incoming.elements]
    # A human editing a model prediction changes its provenance -- that distinction matters
    # for auditing dataset quality and for active-learning loops.
    if shape.source is AnnotationSource.MODEL:
        shape.source = AnnotationSource.MODEL_CORRECTED
    return shape


async def _create_track(
    session: AsyncSession, job: Job, incoming: TrackIn, schema: LabelSchema, actor: User
) -> Track:
    schema.check_shape_type(incoming.label_id, incoming.shape_type)
    if incoming.client_id:
        existing = await session.scalar(
            select(Track).where(Track.job_id == job.id, Track.client_id == incoming.client_id)
        )
        if existing is not None:
            # As for a shape (`_create_shape`): a resend is answered, a reuse refused.
            if (
                existing.label_id == incoming.label_id
                and existing.shape_type == incoming.shape_type
            ):
                return existing
            raise ValidationError(
                f"Client id {incoming.client_id!r} already names track {existing.id} in this "
                "job, which is a different object"
            )
    track = Track(
        job_id=job.id,
        client_id=incoming.client_id,
        label_id=incoming.label_id,
        shape_type=incoming.shape_type,
        group=incoming.group,
        object_id=incoming.object_id,
        source=incoming.source,
        confidence=incoming.confidence,
        attributes=schema.clean_attributes(incoming.label_id, incoming.attributes),
        created_by_id=actor.id,
    )
    session.add(track)
    await session.flush()
    _replace_track_shapes(session, job, track, incoming.shapes, schema)
    await session.flush()
    return track


async def _update_track(
    session: AsyncSession, job: Job, incoming: TrackIn, schema: LabelSchema
) -> Track:
    track = await _load_owned(session, Track, job.id, incoming.id, "Track")
    schema.check_shape_type(incoming.label_id, incoming.shape_type)
    track.label_id = incoming.label_id
    track.shape_type = incoming.shape_type
    track.group = incoming.group
    track.object_id = incoming.object_id
    track.attributes = schema.clean_attributes(incoming.label_id, incoming.attributes)
    if track.source is AnnotationSource.MODEL:
        track.source = AnnotationSource.MODEL_CORRECTED

    await session.execute(delete(TrackShape).where(TrackShape.track_id == track.id))
    _replace_track_shapes(session, job, track, incoming.shapes, schema)
    return track


def _replace_track_shapes(
    session: AsyncSession,
    job: Job,
    track: Track,
    shapes: list[TrackShapeIn],
    schema: LabelSchema,
) -> None:
    mutable = schema.mutable_attribute_names(track.label_id)
    for incoming in sorted(shapes, key=lambda item: item.frame):
        _check_frame(job, incoming.frame)
        # Only mutable attributes may vary per keyframe; the rest live on the track.
        per_frame = {k: v for k, v in incoming.attributes.items() if k in mutable}
        session.add(
            TrackShape(
                track_id=track.id,
                frame=incoming.frame,
                points=incoming.points,
                rotation=incoming.rotation,
                occluded=incoming.occluded,
                outside=incoming.outside,
                keyframe=incoming.keyframe,
                z_order=incoming.z_order,
                attributes=per_frame,
                mask=incoming.mask.model_dump() if incoming.mask else None,
                elements=[element.model_dump(mode="json") for element in incoming.elements],
            )
        )


async def _create_tag(
    session: AsyncSession, job: Job, incoming: TagIn, schema: LabelSchema, actor: User
) -> Tag:
    if incoming.frame is not None:
        _check_frame(job, incoming.frame)
    tag = Tag(
        job_id=job.id,
        client_id=incoming.client_id,
        label_id=incoming.label_id,
        frame=incoming.frame,
        source=incoming.source,
        confidence=incoming.confidence,
        attributes=schema.clean_attributes(incoming.label_id, incoming.attributes),
        created_by_id=actor.id,
    )
    session.add(tag)
    await session.flush()
    return tag


async def _update_tag(session: AsyncSession, job: Job, incoming: TagIn, schema: LabelSchema) -> Tag:
    tag = await _load_owned(session, Tag, job.id, incoming.id, "Tag")
    if incoming.frame is not None:
        _check_frame(job, incoming.frame)
    tag.label_id = incoming.label_id
    tag.frame = incoming.frame
    tag.attributes = schema.clean_attributes(incoming.label_id, incoming.attributes)
    return tag


async def _load_owned(
    session: AsyncSession, model: type[Any], job_id: uuid.UUID, row_id: uuid.UUID | None, label: str
) -> Any:
    if row_id is None:
        raise ValidationError(f"{label} updates require an id")
    row = await session.get(model, row_id)
    if row is None or row.job_id != job_id:
        raise NotFoundError(f"{label} {row_id} not found in this job")
    return row


async def _delete_rows(
    session: AsyncSession, model: type[Any], job_id: uuid.UUID, ids: list[uuid.UUID]
) -> int:
    result = await session.execute(delete(model).where(model.job_id == job_id, model.id.in_(ids)))
    # A DELETE always yields a CursorResult; the generic Result type does not expose
    # rowcount, so this narrows what the driver actually returns.
    return int(cast("CursorResult[Any]", result).rowcount or 0)


def _check_frame(job: Job, frame: int) -> None:
    if not job.contains_frame(frame):
        raise ValidationError(
            f"Frame {frame} is outside this job's range ({job.start_frame}-{job.stop_frame})"
        )


async def refresh_counts(session: AsyncSession, job: Job) -> None:
    """Recompute the job's cached annotation counts.

    Denormalised so that listing 500 jobs never aggregates over the annotation tables.
    """
    for attribute, model in (
        ("shape_count", Shape),
        ("track_count", Track),
        ("tag_count", Tag),
    ):
        count = (
            await session.execute(
                select(func.count()).select_from(model).where(model.job_id == job.id)
            )
        ).scalar_one()
        setattr(job, attribute, int(count))


async def clear_annotations(session: AsyncSession, job: Job) -> None:
    """Remove every annotation in a job. Used by ``replace`` imports."""
    for model in (Shape, Track, Tag):
        await session.execute(delete(model).where(model.job_id == job.id))
    await session.flush()
    await refresh_counts(session, job)


async def decide_suggestions(
    session: AsyncSession,
    job: Job,
    *,
    shape_ids: list[uuid.UUID],
    track_ids: list[uuid.UUID],
    tag_ids: list[uuid.UUID],
    accepted: bool,
) -> dict[str, int]:
    """Accept or reject model-produced annotations.

    Accepting keeps ``source="model"`` so the dataset still records that a model produced
    the geometry and a human agreed with it; rejecting deletes the row.
    """
    counts: dict[str, int] = {}
    for key, model, ids in (
        ("shapes", Shape, shape_ids),
        ("tracks", Track, track_ids),
        ("tags", Tag, tag_ids),
    ):
        if not ids:
            counts[key] = 0
            continue
        if accepted:
            # Cast because mypy 2 joins the three classes in the tuple above to their common
            # declarative base, which has no `confidence`; mypy 1 did not.
            rows = cast(
                "Sequence[Shape | Track | Tag]",
                (
                    await session.execute(
                        select(model).where(model.job_id == job.id, model.id.in_(ids))
                    )
                )
                .scalars()
                .all(),
            )
            for row in rows:
                row.confidence = None
            counts[key] = len(rows)
        else:
            counts[key] = await _delete_rows(session, model, job.id, ids)

    await session.flush()
    job.annotation_version += 1
    await refresh_counts(session, job)
    return counts
