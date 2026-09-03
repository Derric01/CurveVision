"""Projects and the label schema they own.

Labels live on the project and are inherited by every task in it. Schema drift between
tasks in one project is a leading cause of unusable exports, so the default is inheritance
and a task-local override must be explicit.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from curvevision.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from curvevision.core.types import GUID, EnumString, JSONDocument
from curvevision.domain.enums import AttributeType

if TYPE_CHECKING:
    from curvevision.domain.identity import Organization, User
    from curvevision.domain.system import DatasetVersion
    from curvevision.domain.task import Task


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_project_slug"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: When true, any org member with annotator rights may work on unassigned jobs.
    open_assignment: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONDocument, default=dict, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="projects")
    # Eager: every project representation shows its owner, and a lazy load would be an
    # await inside serialisation, which async SQLAlchemy cannot do.
    owner: Mapped[User | None] = relationship(lazy="selectin")
    labels: Mapped[list[Label]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="Label.position",
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="project", cascade="all, delete-orphan")
    dataset_versions: Mapped[list[DatasetVersion]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Label(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A class in the project's label schema.

    ``parent_id`` supports skeleton sub-labels (keypoints): a skeleton label owns one child
    label per joint, and ``skeleton_edges`` holds the connectivity as index pairs.
    """

    __tablename__ = "labels"
    __table_args__ = (UniqueConstraint("project_id", "name", "parent_id", name="uq_label_name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("labels.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    color: Mapped[str] = mapped_column(String(9), default="#38bdf8", nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Restricts which geometry types may carry this label; empty means "any".
    allowed_shape_types: Mapped[list[str]] = mapped_column(
        JSONDocument, default=list, nullable=False
    )
    skeleton_edges: Mapped[list[list[int]]] = mapped_column(
        JSONDocument, default=list, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="labels")
    attributes: Mapped[list[AttributeDefinition]] = relationship(
        back_populates="label",
        cascade="all, delete-orphan",
        order_by="AttributeDefinition.position",
    )
    children: Mapped[list[Label]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped[Label | None] = relationship(back_populates="children", remote_side="Label.id")


class AttributeDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A typed attribute attached to a label.

    ``mutable`` distinguishes "this car is red" (constant over a track) from "this car's
    indicator is on" (may change frame to frame). Getting that distinction wrong is one of
    the more expensive dataset modelling mistakes, so it is explicit here.
    """

    __tablename__ = "attribute_definitions"
    __table_args__ = (UniqueConstraint("label_id", "name", name="uq_attribute_name"),)

    label_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("labels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    attribute_type: Mapped[AttributeType] = mapped_column(
        EnumString(AttributeType, 20), nullable=False
    )
    values: Mapped[list[str]] = mapped_column(JSONDocument, default=list, nullable=False)
    default_value: Mapped[str | None] = mapped_column(String(500))
    mutable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    label: Mapped[Label] = relationship(back_populates="attributes")
