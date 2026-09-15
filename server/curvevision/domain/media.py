"""Media: content-addressed blobs and the per-task assets that reference them.

A blob is the bytes; an asset is a blob's appearance inside one task at one frame index.
Splitting them means uploading the same 4 GB video into three tasks stores it once.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
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
from curvevision.domain.enums import MediaKind

if TYPE_CHECKING:
    from curvevision.domain.task import Task


class MediaBlob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable stored bytes, keyed by content hash."""

    __tablename__ = "media_blobs"

    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    #: Where the bytes live in object storage. Null for a blob annotated in place, whose
    #: bytes were never copied anywhere.
    storage_key: Mapped[str | None] = mapped_column(String(500))
    #: An absolute path on *this machine*, for media the desktop application annotates
    #: where it already sits. A file referenced this way is the user's, not ours: it is
    #: read, never written, and never garbage-collected. Always null on a shared server,
    #: where a path from one request has no meaning to anyone else.
    source_path: Mapped[str | None] = mapped_column(String(4000))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    kind: Mapped[MediaKind] = mapped_column(EnumString(MediaKind, 16), nullable=False)

    # Probed metadata; null when the optional media extras are not installed.
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    frame_count: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    frame_rate: Mapped[float | None] = mapped_column(Float)

    thumbnail_key: Mapped[str | None] = mapped_column(String(500))
    extra: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)


class Asset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One media item inside a task, at a stable ordinal position."""

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("task_id", "position", name="uq_asset_position"),
        Index("ix_asset_task_position", "task_id", "position"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    blob_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("media_blobs.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: First global frame index this asset contributes. Images contribute one frame; a
    #: video contributes ``blob.frame_count``. Lets frame numbers stay contiguous per task.
    start_frame: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frame_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: Whether ``frame_count`` was established by decoding the media, rather than estimated
    #: from container metadata. An image is exact by construction -- it contributes exactly
    #: one frame, and nothing has to be decoded to know that. A video starts False and
    #: becomes True when ``media.probe_task`` counts it.
    #:
    #: False is not a claim that the count is *wrong*. It is the absence of a record that
    #: anything ever checked. The distinction earns its column because the check can decline
    #: (the task already carries annotations, so its frame ranges are not the job's to move)
    #: or fail (a file that moved, a codec this build cannot decode), and either way the task
    #: keeps a number that may overstate the media. An overstated count offers frames that do
    #: not exist, which an annotator meets as what looks like missing media -- so somebody
    #: has to be able to see that the number is provisional.
    frame_count_exact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    task: Mapped[Task] = relationship(back_populates="assets")
    blob: Mapped[MediaBlob] = relationship(lazy="joined")


class MediaChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A contiguous run of decoded frames stored as one object.

    The browser fetches chunks, never individual frames. This is the single most important
    media decision for video annotation throughput.
    """

    __tablename__ = "media_chunks"
    __table_args__ = (
        UniqueConstraint("task_id", "index", "quality", name="uq_media_chunk"),
        Index("ix_media_chunk_task", "task_id", "index"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    quality: Mapped[str] = mapped_column(String(16), default="original", nullable=False)
    start_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), default="application/zip")


class UploadSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Resumable upload state: offset-based ``PATCH`` protocol.

    Large media over a flaky connection is the normal case, not the exception, so uploads
    are resumable from the first release rather than bolted on later.
    """

    __tablename__ = "upload_sessions"

    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    declared_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
