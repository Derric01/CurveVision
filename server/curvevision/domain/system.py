"""Cross-cutting infrastructure tables: background tasks, audit log, webhooks, ML models,
dataset versions.
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
from curvevision.domain.enums import BackgroundTaskState, DatasetVersionState, ModelKind

if TYPE_CHECKING:
    from curvevision.domain.project import Project


class BackgroundTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable record of an asynchronous operation.

    Idempotency is a first-class column: a redelivery after a worker crash finds the
    existing row and resumes rather than duplicating the work.
    """

    __tablename__ = "background_tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_background_task_idempotency"),
        Index("ix_background_task_state", "state", "created_at"),
    )

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[BackgroundTaskState] = mapped_column(
        EnumString(BackgroundTaskState, 16), default=BackgroundTaskState.PENDING, nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(default=0.0, nullable=False)
    message: Mapped[str | None] = mapped_column(String(500))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: Loose reference to what the task is about, e.g. ``("task", <uuid>)``.
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), index=True)


class AuditEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Security-relevant actions: authentication, permission changes, exports, deletions."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_actor_created", "actor_id", "created_at"),)

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)


class Webhook(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "webhooks"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    target_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    events: Mapped[list[str]] = mapped_column(JSONDocument, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )

    deliveries: Mapped[list[WebhookDelivery]] = relationship(
        back_populates="webhook", cascade="all, delete-orphan"
    )


class WebhookDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_delivery_hook", "webhook_id", "created_at"),)

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    webhook: Mapped[Webhook] = relationship(back_populates="deliveries")


class ModelRegistration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An inference model made available to an organization.

    CurveVision never bundles weights. This row says "there is a detector at this URL,
    it emits these labels" -- everything else is the operator's choice, which keeps model
    licenses out of the CurveVision core entirely.
    """

    __tablename__ = "model_registrations"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_model_slug"),)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32), default="http", nullable=False)
    kind: Mapped[ModelKind] = mapped_column(EnumString(ModelKind, 20), nullable=False)
    #: Provider-specific connection settings (endpoint URL, headers, timeouts).
    config: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)
    #: Labels the model emits, used to build the model-label -> project-label mapping UI.
    output_labels: Mapped[list[str]] = mapped_column(JSONDocument, default=list, nullable=False)
    #: The model takes the classes to look for as text, at inference time.
    #:
    #: A first-class column rather than a key in `config`, which is documented as provider
    #: *connection* settings -- endpoint, headers, timeouts. Whether a model has a fixed
    #: label space is a property of the model, not of how CurveVision reaches it, and it
    #: sits next to `output_labels` because the two describe the same thing from opposite
    #: ends: what a closed model can find, and that an open one has no such boundary.
    open_vocabulary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class DatasetVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An immutable snapshot of a project's annotations.

    ``Dataset -> Version -> Annotation -> Review -> Release``. Once released, a version
    cannot be mutated, only superseded -- which is what makes an experiment reproducible.
    """

    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_dataset_version_name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    state: Mapped[DatasetVersionState] = mapped_column(
        EnumString(DatasetVersionState, 16), default=DatasetVersionState.DRAFT, nullable=False
    )
    #: Content hash over the frozen annotation set: two releases with the same hash contain
    #: the same data.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    #: Frozen manifest: task/job ids, frame counts, label schema, statistics.
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)
    statistics: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )

    project: Mapped[Project] = relationship(back_populates="dataset_versions")
