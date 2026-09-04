"""Tasks and jobs.

A **task** owns media and configuration. A **job** owns *work*: a contiguous frame range,
one assignee, one state, one review outcome. That split is what lets five people annotate
one 10,000-frame video in parallel while a sixth reviews it.

CurveVision deliberately does not have a separate ``Segment`` entity between the two: the
frame range lives on the job, which removes a join from the hottest query in the system.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from curvevision.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from curvevision.core.types import GUID, EnumString, JSONDocument
from curvevision.domain.enums import JobKind, JobState, MediaKind, TaskStatus

if TYPE_CHECKING:
    from curvevision.domain.identity import User
    from curvevision.domain.media import Asset
    from curvevision.domain.project import Project


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_task_project_created", "project_id", "created_at"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        EnumString(TaskStatus, 20), default=TaskStatus.DRAFT, nullable=False
    )
    media_kind: Mapped[MediaKind] = mapped_column(
        EnumString(MediaKind, 16), default=MediaKind.IMAGE, nullable=False
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    #: Total frames across all assets. Denormalised because every job listing needs it.
    frame_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Frames per job when jobs are generated. 0 means "one job for the whole task".
    segment_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Overlap in frames between consecutive jobs, useful for tracking continuity.
    overlap: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)

    project: Mapped[Project] = relationship(back_populates="tasks")
    owner: Mapped[User | None] = relationship(lazy="selectin", foreign_keys=[owner_id])
    assignee: Mapped[User | None] = relationship(lazy="selectin", foreign_keys=[assignee_id])
    assets: Mapped[list[Asset]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="Asset.position"
    )
    jobs: Mapped[list[Job]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="Job.index"
    )


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("task_id", "index", name="uq_job_index"),
        Index("ix_job_assignee_state", "assignee_id", "state"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[JobKind] = mapped_column(
        EnumString(JobKind, 20), default=JobKind.ANNOTATION, nullable=False
    )
    state: Mapped[JobState] = mapped_column(
        EnumString(JobState, 20), default=JobState.NEW, nullable=False
    )

    start_frame: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stop_frame: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Monotonic counter bumped on every annotation write. Clients send the version they
    #: read; a mismatch means someone else edited the job and the write is rejected.
    annotation_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Cached counts so job listings never aggregate over the annotation tables.
    shape_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    track_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tag_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    task: Mapped[Task] = relationship(back_populates="jobs")
    assignee: Mapped[User | None] = relationship(lazy="selectin", foreign_keys=[assignee_id])
    reviewer: Mapped[User | None] = relationship(lazy="selectin", foreign_keys=[reviewer_id])

    @property
    def frame_count(self) -> int:
        return max(0, self.stop_frame - self.start_frame + 1)

    def contains_frame(self, frame: int) -> bool:
        return self.start_frame <= frame <= self.stop_frame
