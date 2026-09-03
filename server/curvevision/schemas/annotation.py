"""Annotation payloads.

The write API is a **batch document**: the editor sends everything it created, updated and
deleted since the last save in one request, together with the ``annotation_version`` it
read. That keeps autosave to one round trip and makes concurrent-edit detection exact.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import Field, model_validator

from curvevision.domain.enums import SHAPE_MIN_POINTS, AnnotationSource, ShapeType
from curvevision.schemas.common import ORMModel, StrictModel


class MaskData(StrictModel):
    """Run-length encoded binary mask plus its bounding box.

    RLE keeps a 4K instance mask in the low kilobytes; the box makes spatial queries and
    rendering possible without decoding.
    """

    rle: list[int]
    left: int = Field(ge=0)
    top: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SkeletonElement(StrictModel):
    label_id: uuid.UUID
    points: list[float] = Field(min_length=2, max_length=2)
    occluded: bool = False
    outside: bool = False


class _GeometryMixin(StrictModel):
    shape_type: ShapeType
    points: list[float] = Field(default_factory=list)
    rotation: float = 0.0
    occluded: bool = False
    outside: bool = False
    z_order: int = 0
    mask: MaskData | None = None
    elements: list[SkeletonElement] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_geometry(self) -> _GeometryMixin:
        if len(self.points) % 2 != 0:
            raise ValueError("points must contain an even number of coordinates")
        if self.shape_type is ShapeType.MASK:
            if self.mask is None:
                raise ValueError("mask shapes require a mask payload")
            return self
        if self.shape_type is ShapeType.SKELETON:
            if not self.elements:
                raise ValueError("skeleton shapes require at least one element")
            return self
        minimum = SHAPE_MIN_POINTS[self.shape_type]
        if len(self.points) // 2 < minimum:
            raise ValueError(
                f"{self.shape_type} requires at least {minimum} points, got {len(self.points) // 2}"
            )
        return self


class ShapeIn(_GeometryMixin):
    id: uuid.UUID | None = None
    client_id: str | None = Field(default=None, max_length=64)
    label_id: uuid.UUID
    frame: int = Field(ge=0)
    group: int | None = None
    source: AnnotationSource = AnnotationSource.MANUAL
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class TrackShapeIn(_GeometryMixin):
    frame: int = Field(ge=0)
    keyframe: bool = True
    attributes: dict[str, Any] = Field(default_factory=dict)


class TrackIn(StrictModel):
    id: uuid.UUID | None = None
    client_id: str | None = Field(default=None, max_length=64)
    label_id: uuid.UUID
    shape_type: ShapeType
    group: int | None = None
    object_id: int | None = None
    source: AnnotationSource = AnnotationSource.MANUAL
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    attributes: dict[str, Any] = Field(default_factory=dict)
    shapes: list[TrackShapeIn] = Field(min_length=1)

    @model_validator(mode="after")
    def _frames_unique(self) -> TrackIn:
        frames = [shape.frame for shape in self.shapes]
        if len(set(frames)) != len(frames):
            raise ValueError("a track may not have two keyframes on the same frame")
        return self


class TagIn(StrictModel):
    id: uuid.UUID | None = None
    client_id: str | None = Field(default=None, max_length=64)
    label_id: uuid.UUID
    frame: int | None = Field(default=None, ge=0)
    source: AnnotationSource = AnnotationSource.MANUAL
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class ShapeOut(ORMModel):
    id: uuid.UUID
    client_id: str | None = None
    label_id: uuid.UUID
    frame: int
    shape_type: ShapeType
    points: list[float]
    rotation: float
    occluded: bool
    outside: bool
    z_order: int
    group: int | None = None
    source: AnnotationSource
    confidence: float | None = None
    attributes: dict[str, Any]
    mask: dict[str, Any] | None = None
    elements: list[dict[str, Any]] = Field(default_factory=list)


class TrackShapeOut(ORMModel):
    frame: int
    points: list[float]
    rotation: float
    occluded: bool
    outside: bool
    keyframe: bool
    z_order: int
    attributes: dict[str, Any]
    mask: dict[str, Any] | None = None
    elements: list[dict[str, Any]] = Field(default_factory=list)


class TrackOut(ORMModel):
    id: uuid.UUID
    client_id: str | None = None
    label_id: uuid.UUID
    shape_type: ShapeType
    group: int | None = None
    object_id: int | None = None
    source: AnnotationSource
    confidence: float | None = None
    attributes: dict[str, Any]
    shapes: list[TrackShapeOut]


class TagOut(ORMModel):
    id: uuid.UUID
    client_id: str | None = None
    label_id: uuid.UUID
    frame: int | None = None
    source: AnnotationSource
    confidence: float | None = None
    attributes: dict[str, Any]


class AnnotationDocument(StrictModel):
    """Everything annotated in one job (or one frame range of it)."""

    job_id: uuid.UUID
    annotation_version: int
    shapes: list[ShapeOut] = Field(default_factory=list)
    tracks: list[TrackOut] = Field(default_factory=list)
    tags: list[TagOut] = Field(default_factory=list)


class AnnotationWrite(StrictModel):
    """One autosave batch.

    ``annotation_version`` is the version the client last read. The server rejects the
    write if the job has moved on, so two annotators cannot silently clobber each other.
    Pass ``None`` to skip the check (used by imports and by scripted writes).
    """

    annotation_version: int | None = None
    created_shapes: list[ShapeIn] = Field(default_factory=list)
    updated_shapes: list[ShapeIn] = Field(default_factory=list)
    deleted_shapes: list[uuid.UUID] = Field(default_factory=list)
    created_tracks: list[TrackIn] = Field(default_factory=list)
    updated_tracks: list[TrackIn] = Field(default_factory=list)
    deleted_tracks: list[uuid.UUID] = Field(default_factory=list)
    created_tags: list[TagIn] = Field(default_factory=list)
    updated_tags: list[TagIn] = Field(default_factory=list)
    deleted_tags: list[uuid.UUID] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            (
                self.created_shapes,
                self.updated_shapes,
                self.deleted_shapes,
                self.created_tracks,
                self.updated_tracks,
                self.deleted_tracks,
                self.created_tags,
                self.updated_tags,
                self.deleted_tags,
            )
        )


class AnnotationWriteResult(StrictModel):
    job_id: uuid.UUID
    annotation_version: int
    created: dict[str, int]
    updated: dict[str, int]
    deleted: dict[str, int]
    #: Maps the client's temporary ids to the persisted server ids.
    id_map: dict[str, uuid.UUID] = Field(default_factory=dict)


class FrameAnnotations(StrictModel):
    """Everything visible on one frame, with tracks already interpolated."""

    frame: int
    shapes: list[ShapeOut]
    tags: list[TagOut]
