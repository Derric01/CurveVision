"""Review: issues, comments and annotation history.

An **issue** is a positioned, threaded comment anchored to a frame and optionally to a
shape -- the mechanism by which a reviewer sends work back with enough context to act on.
An **annotation event** is the append-only history that makes per-object blame and rollback
possible.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from curvevision.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from curvevision.core.types import GUID, EnumString, JSONDocument
from curvevision.domain.enums import IssueState

if TYPE_CHECKING:
    from curvevision.domain.identity import User


class Issue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "issues"
    __table_args__ = (Index("ix_issue_job_state", "job_id", "state"),)

    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    frame: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Optional anchor point in image space, so the reviewer can point at the problem.
    position: Mapped[list[float]] = mapped_column(JSONDocument, default=list, nullable=False)
    #: Optional link to the offending shape or track.
    shape_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("shapes.id", ondelete="SET NULL")
    )
    track_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("tracks.id", ondelete="SET NULL")
    )
    state: Mapped[IssueState] = mapped_column(
        EnumString(IssueState, 16), default=IssueState.OPEN, nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    comments: Mapped[list[Comment]] = relationship(
        back_populates="issue", cascade="all, delete-orphan", order_by="Comment.created_at"
    )


class Comment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "comments"

    issue_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("issues.id", ondelete="CASCADE"), index=True, nullable=False
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    issue: Mapped[Issue] = relationship(back_populates="comments")
    author: Mapped[User | None] = relationship()


class AnnotationEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only annotation history.

    One row per write batch rather than per object: a 500-shape paste is one event with a
    summary, which keeps history readable and the table bounded.
    """

    __tablename__ = "annotation_events"
    __table_args__ = (Index("ix_annotation_event_job_created", "job_id", "created_at"),)

    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # create|update|delete|import
    #: Counts by annotation kind, plus enough detail to explain the change in the UI.
    summary: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)
    annotation_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class QualityReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Result of comparing an annotation job against a ground-truth job."""

    __tablename__ = "quality_reports"

    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    ground_truth_job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    iou_threshold: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    precision: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recall: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    f1: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    #: Per-label breakdown and the list of conflicting objects.
    details: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)
