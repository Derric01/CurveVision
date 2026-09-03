"""Annotations: shapes, tracks and tags.

Three kinds, three tables, rather than one table with a forest of nullable columns:

* ``Shape``  -- one geometry on one frame.
* ``Track``  -- one object over time: ``TrackShape`` keyframes plus interpolation.
* ``Tag``    -- a classification on a frame, or on the whole job.

Geometry is a flat ``float[]`` of image-pixel coordinates plus a type discriminator.
Attribute values are a validated JSON object rather than a side table: they are always read
with their parent object and never queried independently, so a row per value would triple
row counts for no query benefit. Validation happens at the service boundary against the
label's attribute schema.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from curvevision.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from curvevision.core.types import GUID, EnumString, JSONDocument
from curvevision.domain.enums import AnnotationSource, ShapeType

if TYPE_CHECKING:
    from curvevision.domain.project import Label
    from curvevision.domain.task import Job


class AnnotationBase(UUIDPrimaryKeyMixin, TimestampMixin):
    """Columns every annotation kind carries.

    SQLAlchemy copies ``mapped_column`` definitions (foreign keys included) from a mixin
    into each mapped subclass, so these are real per-table columns, not shared ones.
    """

    label_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("labels.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    source: Mapped[AnnotationSource] = mapped_column(
        EnumString(AnnotationSource, 20), default=AnnotationSource.MANUAL, nullable=False
    )
    #: Model confidence in [0, 1] when ``source`` is model-derived; null for manual work.
    confidence: Mapped[float | None] = mapped_column(Float)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: Client-supplied identifier, unique within a job. Lets the editor reconcile optimistic
    #: local objects with server ids without a round trip per object.
    client_id: Mapped[str | None] = mapped_column(String(64))


class Shape(AnnotationBase, Base):
    __tablename__ = "shapes"
    __table_args__ = (
        Index("ix_shape_job_frame", "job_id", "frame"),
        Index("ix_shape_job_label", "job_id", "label_id"),
        UniqueConstraint("job_id", "client_id", name="uq_shape_client_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    frame: Mapped[int] = mapped_column(Integer, nullable=False)
    shape_type: Mapped[ShapeType] = mapped_column(EnumString(ShapeType, 24), nullable=False)
    #: ``[x1, y1, x2, y2, ...]`` in image pixel space.
    points: Mapped[list[float]] = mapped_column(JSONDocument, nullable=False)
    rotation: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    occluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    outside: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    z_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Group id for shapes that form one logical object (e.g. a split occlusion).
    group: Mapped[int | None] = mapped_column(Integer)
    #: RLE payload for masks: ``{"rle": [...], "left": int, "top": int, "width": int,
    #: "height": int}``. Keeps a 4K instance mask in the low kilobytes.
    mask: Mapped[dict[str, Any] | None] = mapped_column(JSONDocument)
    #: Nested keypoint shapes for skeletons, as ``[{label_id, points, occluded, outside}]``.
    elements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONDocument, default=list, nullable=False
    )

    job: Mapped[Job] = relationship()
    label: Mapped[Label] = relationship()


class Track(AnnotationBase, Base):
    """One object followed across frames."""

    __tablename__ = "tracks"
    __table_args__ = (
        Index("ix_track_job", "job_id"),
        UniqueConstraint("job_id", "client_id", name="uq_track_client_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    shape_type: Mapped[ShapeType] = mapped_column(EnumString(ShapeType, 24), nullable=False)
    group: Mapped[int | None] = mapped_column(Integer)
    #: Stable object identity across the dataset, surfaced in exports that support it.
    object_id: Mapped[int | None] = mapped_column(Integer, index=True)

    job: Mapped[Job] = relationship()
    label: Mapped[Label] = relationship()
    shapes: Mapped[list[TrackShape]] = relationship(
        back_populates="track", cascade="all, delete-orphan", order_by="TrackShape.frame"
    )


class TrackShape(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A keyframe of a track.

    ``outside`` means the object is absent from this frame onward until the next keyframe;
    ``occluded`` means present but hidden; ``keyframe`` distinguishes a human-authored
    position from a materialised interpolation. These three flags encode the situations
    that actually occur in video, and their absence is a common design failure.
    """

    __tablename__ = "track_shapes"
    __table_args__ = (
        UniqueConstraint("track_id", "frame", name="uq_track_shape_frame"),
        Index("ix_track_shape_track_frame", "track_id", "frame"),
    )

    track_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False
    )
    frame: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[list[float]] = mapped_column(JSONDocument, nullable=False)
    rotation: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    occluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    outside: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    keyframe: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    z_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Values of mutable attributes at this keyframe only.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)
    mask: Mapped[dict[str, Any] | None] = mapped_column(JSONDocument)
    elements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONDocument, default=list, nullable=False
    )

    track: Mapped[Track] = relationship(back_populates="shapes")


class Tag(AnnotationBase, Base):
    """Frame-level or job-level classification."""

    __tablename__ = "tags"
    __table_args__ = (
        Index("ix_tag_job_frame", "job_id", "frame"),
        UniqueConstraint("job_id", "client_id", name="uq_tag_client_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    #: Null means the tag applies to the whole job rather than one frame.
    frame: Mapped[int | None] = mapped_column(Integer)

    job: Mapped[Job] = relationship()
    label: Mapped[Label] = relationship()
