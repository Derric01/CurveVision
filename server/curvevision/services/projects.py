"""Projects and their label schema."""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncScalarResult, AsyncSession
from sqlalchemy.orm import selectinload

from curvevision.core.errors import ConflictError, NotFoundError, ValidationError
from curvevision.domain.annotation import Shape, Tag, Track, TrackShape
from curvevision.domain.enums import AttributeType, JobState
from curvevision.domain.identity import User
from curvevision.domain.project import AttributeDefinition, Label, Project
from curvevision.domain.task import Job, Task
from curvevision.schemas.project import AttributeIn, LabelIn


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
    if payload.name != label.name:
        # `create_label` has always refused a duplicate name and this did not check at all,
        # so the rule could be walked straight around with a rename. Two labels of one name
        # is not a schema: exports key classes by name -- COCO categories, a YOLO class
        # list, this project's own class distribution -- so the duplicate does not stay a
        # cosmetic problem for long.
        duplicate = await session.execute(
            select(Label).where(
                Label.project_id == label.project_id,
                Label.name == payload.name,
                Label.parent_id == label.parent_id,
                Label.id != label.id,
            )
        )
        if duplicate.scalar_one_or_none() is not None:
            raise ConflictError(f"A label named {payload.name!r} already exists in this project")

    # Attributes are replaced wholesale, but existing rows are reused where the client sent
    # an id. That is not enough on its own to keep annotation values: they are keyed by
    # attribute *name*, so `_refuse_stranding_values` decides what may change, before
    # anything about the label has been touched.
    await session.refresh(label, ["attributes"])
    by_id = {attribute.id: attribute for attribute in label.attributes}
    await _refuse_stranding_values(session, label, by_id, payload.attributes)
    kept = {incoming.id for incoming in payload.attributes if incoming.id in by_id}

    label.name = payload.name
    label.color = payload.color
    label.position = payload.position
    label.allowed_shape_types = [shape.value for shape in payload.allowed_shape_types]
    label.skeleton_edges = payload.skeleton_edges

    # Removals and renames are written before anything else, each flushed on its own, so the
    # final names can be assigned freely afterwards: `uq_attribute_name` is checked row by
    # row, and a new attribute taking a removed one's name, or two attributes trading names,
    # would otherwise trip it halfway through the flush.
    for attribute in label.attributes:
        if attribute.id not in kept:
            await session.delete(attribute)
    renamed = [
        by_id[incoming.id]
        for incoming in payload.attributes
        if incoming.id in by_id and by_id[incoming.id].name != incoming.name
    ]
    for attribute in renamed:
        attribute.name = f"~{attribute.id}"
    await session.flush()

    for index, incoming in enumerate(payload.attributes):
        if incoming.id is not None and incoming.id in by_id:
            attribute = by_id[incoming.id]
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

    await session.flush()
    return label


async def _refuse_stranding_values(
    session: AsyncSession,
    label: Label,
    current: dict[uuid.UUID, AttributeDefinition],
    incoming: list[AttributeIn],
) -> None:
    """Refuse an attribute edit that would leave recorded values unable to be saved again.

    Values live on each annotation as a JSON object keyed by attribute name, and
    `validate_attributes` rejects a key the schema does not declare. So an edit that
    removes or renames an attribute, changes its type or narrows its options does not fail
    itself: it fails the *next save* of every annotation carrying the old value, and the
    annotator is the one who finds out, from an autosave that is refused.

    For an attribute that already exists, the rules are CVAT's (`cvat-ai/cvat`,
    `LabelSerializer._update_attribute`, MIT; the convention only, no code): its type and
    its `mutable` flag are fixed, and a select's options may be added to but not taken
    away. Those hold whether or not anything is recorded yet, so the rule a person meets
    does not depend on data they cannot see; removing the attribute and adding a new one
    is how to change them. CVAT allows a rename and a removal because it stores values
    against the attribute's id. This codebase stores them against its name, so here those
    two are refused only while an annotation of the label records a value under it —
    which is what keeps a schema correctable before it has been used.
    """
    kept = {attribute.id: attribute for attribute in incoming if attribute.id in current}
    to_count: list[tuple[str, str]] = []  # (attribute name, what is being done to it)
    required_now: list[str] = []

    for attribute_id, new in kept.items():
        old = current[attribute_id]
        if new.attribute_type != old.attribute_type:
            raise ConflictError(
                f"The type of the attribute {old.name!r} cannot change. "
                "Remove it and add a new attribute instead."
            )
        # Turning `mutable` off would silently drop every value recorded per keyframe on
        # the track's next save; CVAT fixes it in both directions, and so does this.
        if new.mutable != old.mutable:
            raise ConflictError(
                f"Whether the attribute {old.name!r} can change from frame to frame cannot "
                "be changed. Remove it and add a new attribute instead."
            )
        if old.attribute_type in (AttributeType.SELECT, AttributeType.RADIO):
            dropped = [value for value in old.values if value not in new.values]
            if dropped:
                raise ConflictError(
                    f"The attribute {old.name!r} cannot lose its options "
                    f"({', '.join(dropped)}): options may be added, not removed."
                )
        if new.name != old.name:
            to_count.append((old.name, "renamed"))
        if _demands_a_value(new) and not _demands_a_value(old):
            required_now.append(new.name)

    to_count.extend(
        (attribute.name, "removed")
        for attribute_id, attribute in current.items()
        if attribute_id not in kept
    )
    required_now.extend(
        attribute.name
        for attribute in incoming
        if attribute.id not in current and _demands_a_value(attribute)
    )
    if not to_count and not required_now:
        return

    total, recorded, answered = await _attribute_usage(session, label.id)
    for name, change in to_count:
        if recorded[name]:
            raise ConflictError(
                f"{recorded[name]} annotation(s) of {label.name!r} record a value for the "
                f"attribute {name!r}, so it cannot be {change}: values are stored by "
                "attribute name, and theirs could not be saved again."
            )
    for name in required_now:
        missing = total - answered[name]
        if missing:
            raise ConflictError(
                f"{missing} annotation(s) of {label.name!r} have no value for {name!r}, so "
                "it cannot be required without a default: each would be refused on its "
                "next save. Give it a default value."
            )


def _demands_a_value(attribute: AttributeDefinition | AttributeIn) -> bool:
    """Whether a save without this attribute is refused, rather than given its default."""
    return attribute.required and attribute.default_value is None


async def _attribute_usage(
    session: AsyncSession, label_id: uuid.UUID
) -> tuple[int, Counter[str], Counter[str]]:
    """How the annotations of one label use its attributes.

    Returns the number of annotations, how many record a value under each attribute name
    anywhere (a track counts once, whether the value is on the track or on a keyframe), and
    how many carry one at the level a save validates against — which for a track is the
    track itself, since keyframes carry mutable values only and are not checked for
    required ones.

    Read in Python rather than asked of the database by JSON path, deliberately: the path
    syntax differs between SQLite and PostgreSQL, SQLAlchemy's SQLite rendering does not
    escape a quote in the key, and attribute names are free text. This runs only for an
    edit that removes, renames or newly requires an attribute, which is rare and
    deliberate, and it streams rather than loading every row at once.
    """
    total = 0
    recorded: Counter[str] = Counter()
    answered: Counter[str] = Counter()

    for model in (Shape, Tag):
        rows: AsyncScalarResult[dict[str, Any]] = await session.stream_scalars(
            select(model.attributes).where(model.label_id == label_id)
        )
        async for values in rows:
            total += 1
            recorded.update(values.keys())
            answered.update(values.keys())

    per_track: dict[uuid.UUID, set[str]] = {}
    tracks = await session.stream(
        select(Track.id, Track.attributes).where(Track.label_id == label_id)
    )
    async for track_id, values in tracks:
        per_track[track_id] = set(values)
        answered.update(values.keys())
    keyframes = await session.stream(
        select(TrackShape.track_id, TrackShape.attributes)
        .join(Track, TrackShape.track_id == Track.id)
        .where(Track.label_id == label_id)
    )
    async for track_id, values in keyframes:
        per_track[track_id].update(values)
    total += len(per_track)
    for names in per_track.values():
        recorded.update(names)

    return total, recorded, answered


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
