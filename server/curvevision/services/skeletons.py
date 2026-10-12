"""A skeleton's joints, as this project stores them.

A joint is stored as `{label_id, points, occluded, outside}`, `label_id` naming one of the
skeleton label's child labels, in the label's declared order — the order the editor places
joints in and every exporter writes them in. Anything arriving from outside the project
(an imported archive, a model's prediction) cannot know those ids, and names its joints
instead. This is where a name becomes an id; the joints of a skeleton that reach the
database from outside go through it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from curvevision.domain.project import Label


def resolve_joints(
    elements: list[dict[str, Any]], joints: list[tuple[str, str]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """An imported skeleton's joints as this project stores them, and the names it lacked.

    `joints` is the target label's `(id, name)` pairs in declared order, which is the order
    the editor places joints in and every exporter writes them in. A joint is found by its
    name; an archive whose joints carry no names (a native export from before they did) is
    matched by position, which is how formats store them. A joint the archive does not have
    is recorded as one nobody could see, as the editor records a skipped joint, so the list
    still lines up with the label's.
    """
    by_name = {str(element["name"]): element for element in elements if element.get("name")}
    unknown = [name for name in by_name if name not in {joint_name for _, joint_name in joints}]
    resolved = []
    for index, (joint_id, joint_name) in enumerate(joints):
        if by_name:
            element = by_name.get(joint_name)
        else:
            element = elements[index] if index < len(elements) else None
        points = list(element.get("points") or [0.0, 0.0]) if element else [0.0, 0.0]
        resolved.append(
            {
                "label_id": joint_id,
                "points": [float(points[0]), float(points[1])] if len(points) >= 2 else [0.0, 0.0],
                "occluded": bool(element.get("occluded", False)) if element else False,
                "outside": bool(element.get("outside", False)) if element else True,
            }
        )
    return resolved, unknown


async def skeleton_joints(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[str, list[tuple[str, str]]]:
    """Each skeleton label's joints, `(id, name)` in declared order, by the label's id."""
    labels = (
        (
            await session.execute(
                select(Label)
                .where(Label.project_id == project_id, Label.parent_id.is_(None))
                .options(selectinload(Label.children))
            )
        )
        .scalars()
        .all()
    )
    return {
        str(label.id): [
            (str(child.id), child.name)
            for child in sorted(label.children, key=lambda child: child.position)
        ]
        for label in labels
        if label.children
    }
