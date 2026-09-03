"""Projects and their label schema."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from curvevision.core.errors import ConflictError, NotFoundError, ValidationError
from curvevision.domain.annotation import Shape, Tag, Track
from curvevision.domain.enums import AttributeType, JobState
from curvevision.domain.identity import User
from curvevision.domain.project import AttributeDefinition, Label, Project
from curvevision.domain.task import Job, Task
from curvevision.schemas.project import LabelIn


async def create_project(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    slug: str,
    name: str,
    description: str | None,
    open_assignment: bool,
    labels: list[LabelIn],
    owner: User,
) -> Project:
    existing = await session.execute(
        select(Project).where(Project.organization_id == organization_id, Project.slug == slug)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"A project with the slug {slug!r} already exists here")

    project = Project(
        organization_id=organization_id,
        slug=slug,
        name=name,
        description=description,
        open_assignment=open_assignment,
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()

    for position, label in enumerate(labels):
        await create_label(session, project, label, default_position=position)

    await session.flush()
    return project


async def get_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def load_project_labels(session: AsyncSession, project_id: uuid.UUID) -> list[Label]:
    result = await session.execute(
        select(Label)
        .where(Label.project_id == project_id, Label.parent_id.is_(None))
        .options(
            selectinload(Label.attributes),
            selectinload(Label.children).selectinload(Label.attributes),
            # One level deeper than the schema actually nests: serialising a keypoint
            # child still *reads* its (empty) children, and an unloaded read is a lazy
            # load, which async SQLAlchemy cannot service mid-serialisation.
            selectinload(Label.children).selectinload(Label.children),
        )
        .order_by(Label.position, Label.name)
    )
    return list(result.scalars().all())


async def create_label(
    session: AsyncSession,
    project: Project,
    payload: LabelIn,
    *,
    parent: Label | None = None,
    default_position: int = 0,
) -> Label:
    duplicate = await session.execute(
        select(Label).where(
            Label.project_id == project.id,
            Label.name == payload.name,
            Label.parent_id == (parent.id if parent else None),
        )
    )
    if duplicate.scalar_one_or_none() is not None:
        raise ConflictError(f"A label named {payload.name!r} already exists in this project")

    label = Label(
        project_id=project.id,
        parent_id=parent.id if parent else None,
        name=payload.name,
        color=payload.color,
        position=payload.position or default_position,
        allowed_shape_types=[shape.value for shape in payload.allowed_shape_types],
        skeleton_edges=payload.skeleton_edges,
    )
    session.add(label)
    await session.flush()

    for index, attribute in enumerate(payload.attributes):
        session.add(
            AttributeDefinition(
                label_id=label.id,
                name=attribute.name,
                attribute_type=attribute.attribute_type,
                values=attribute.values,
                default_value=attribute.default_value,
                mutable=attribute.mutable,
                required=attribute.required,
                position=attribute.position or index,
            )
        )

    for index, child in enumerate(payload.children):
        await create_label(session, project, child, parent=label, default_position=index)

    await session.flush()
    return label


async def update_label(session: AsyncSession, label: Label, payload: LabelIn) -> Label:
    label.name = payload.name
    label.color = payload.color
    label.position = payload.position
    label.allowed_shape_types = [shape.value for shape in payload.allowed_shape_types]
    label.skeleton_edges = payload.skeleton_edges

    # Attributes are replaced wholesale, but existing rows are reused where the client sent
    # an id, so annotation values keyed by attribute name survive an edit.
    await session.refresh(label, ["attributes"])
    by_id = {attribute.id: attribute for attribute in label.attributes}
    keep: set[uuid.UUID] = set()

    for index, incoming in enumerate(payload.attributes):
        if incoming.id is not None and incoming.id in by_id:
            attribute = by_id[incoming.id]
            keep.add(attribute.id)
        else:
            attribute = AttributeDefinition(label_id=label.id)
            session.add(attribute)
        attribute.name = incoming.name
        attribute.attribute_type = incoming.attribute_type
        attribute.values = incoming.values
        attribute.default_value = incoming.default_value
        attribute.mutable = incoming.mutable
        attribute.required = incoming.required
        attribute.position = incoming.position or index

    for attribute in label.attributes:
        if attribute.id not in keep and attribute.id in by_id:
            await session.delete(attribute)

    await session.flush()
    return label


async def delete_label(session: AsyncSession, label: Label) -> None:
    """Refuse to delete a label that annotations still reference.

    Cascading would silently destroy annotation work. Making the caller migrate or delete
    those annotations first is the safe default.
    """
    for model in (Shape, Track, Tag):
        used = await session.execute(
            select(func.count()).select_from(model).where(model.label_id == label.id)
        )
        count = used.scalar_one()
        if count:
            raise ConflictError(
                f"{count} annotation(s) still use the label {label.name!r}. "
                "Delete or relabel them first."
            )
    await session.delete(label)


def validate_attributes(
    values: dict[str, Any], definitions: list[AttributeDefinition]
) -> dict[str, Any]:
    """Validate annotation attribute values against a label's schema.

    Unknown keys are rejected rather than dropped: silently discarding an annotator's work
    because of a typo in a script is worse than a clear error.
    """
    by_name = {definition.name: definition for definition in definitions}
    unknown = set(values) - set(by_name)
    if unknown:
        raise ValidationError(f"Unknown attribute(s) for this label: {', '.join(sorted(unknown))}")

    cleaned: dict[str, Any] = {}
    for name, definition in by_name.items():
        if name not in values:
            if definition.required and definition.default_value is None:
                raise ValidationError(f"Attribute {name!r} is required")
            if definition.default_value is not None:
                cleaned[name] = definition.default_value
            continue
        cleaned[name] = _coerce_attribute(name, values[name], definition)
    return cleaned


def _coerce_attribute(name: str, value: Any, definition: AttributeDefinition) -> Any:
    match definition.attribute_type:
        case AttributeType.CHECKBOX:
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
            raise ValidationError(f"Attribute {name!r} expects a boolean")
        case AttributeType.NUMBER:
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"Attribute {name!r} expects a number") from exc
        case AttributeType.SELECT | AttributeType.RADIO:
            text = str(value)
            if text not in definition.values:
                raise ValidationError(
                    f"Attribute {name!r} must be one of: {', '.join(definition.values)}"
                )
            return text
        case _:
            return str(value)


async def project_statistics(session: AsyncSession, project_id: uuid.UUID) -> dict[str, Any]:
    """Aggregate counts for a project. One query per metric, all indexed."""
    task_ids_subq = select(Task.id).where(Task.project_id == project_id).scalar_subquery()
    job_ids_subq = select(Job.id).where(Job.task_id.in_(task_ids_subq)).scalar_subquery()

    task_count = (
        await session.execute(
            select(func.count()).select_from(Task).where(Task.project_id == project_id)
        )
    ).scalar_one()
    frame_count = (
        await session.execute(
            select(func.coalesce(func.sum(Task.frame_count), 0)).where(
                Task.project_id == project_id
            )
        )
    ).scalar_one()

    job_rows = (
        await session.execute(
            select(Job.state, func.count())
            .where(Job.task_id.in_(task_ids_subq))
            .group_by(Job.state)
        )
    ).all()
    jobs_by_state = {str(state): int(count) for state, count in job_rows}

    counts: dict[str, int] = {}
    for key, model in (("shape_count", Shape), ("track_count", Track), ("tag_count", Tag)):
        counts[key] = int(
            (
                await session.execute(
                    select(func.count()).select_from(model).where(model.job_id.in_(job_ids_subq))
                )
            ).scalar_one()
        )

    distribution_rows = (
        await session.execute(
            select(Label.name, func.count(Shape.id))
            .join(Shape, Shape.label_id == Label.id)
            .where(Shape.job_id.in_(job_ids_subq))
            .group_by(Label.name)
            .order_by(func.count(Shape.id).desc())
        )
    ).all()

    annotated_frames = (
        await session.execute(
            select(func.count(func.distinct(Shape.frame))).where(Shape.job_id.in_(job_ids_subq))
        )
    ).scalar_one()

    return {
        "project_id": project_id,
        "task_count": int(task_count),
        "job_count": sum(jobs_by_state.values()),
        "frame_count": int(frame_count),
        **counts,
        "label_distribution": {name: int(count) for name, count in distribution_rows},
        "jobs_by_state": {state.value: jobs_by_state.get(state.value, 0) for state in JobState},
        "frames_without_annotations": max(0, int(frame_count) - int(annotated_frames)),
    }
