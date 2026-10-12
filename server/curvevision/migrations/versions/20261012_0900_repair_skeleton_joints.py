"""Repair skeleton joints stored before iteration 60

A joint is stored as ``{label_id, points, occluded, outside}``, ``label_id`` one of its
skeleton label's child labels, in the label's declared order. Before iteration 60 three
paths stored something else, and those rows are still in any database that took one:

* a COCO import, and a model's predicted skeleton, stored each joint by **name**;
* a native-format import carried **another project's** joint ids, which name nothing here;
* an import that created its skeleton label created it with **no joints at all**.

The editor cannot draw such a joint, every exporter drops it, and the API could not type
``elements`` while they existed. This rewrites each skeleton — in ``shapes``, and in
``track_shapes`` through their track's label — the way ``services.skeletons.resolve_joints``
resolves an import today: by name where the joints carry names, by position where they do
not, one entry per declared joint in declared order, a joint the row lacks recorded as one
nobody could see.

**A label with no joints gets them**, named as its skeletons named them, in the order they
first appear (``joint 1``, ``joint 2``… where they carried no names) — which is what an
import has done since iteration 60. Deleting those skeletons, or emptying them, would lose
annotation somebody made to fix a schema they did not.

The resolution is inlined rather than imported: a migration must do tomorrow what it does
today, whatever the service becomes. It is idempotent — a row already in the stored form is
left byte-for-byte alone — so the downgrade has nothing to undo and is a no-op: the repaired
rows are valid under the old schema too.

Revision ID: 3f6d2a91c7e5
Revises: b5e0c72f18d4
Create Date: 2026-10-12 09:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

from curvevision.core.types import GUID, JSONDocument, UTCDateTime

revision: str = "3f6d2a91c7e5"
down_revision: str | None = "b5e0c72f18d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_labels = sa.table(
    "labels",
    sa.column("id", GUID()),
    sa.column("project_id", GUID()),
    sa.column("parent_id", GUID()),
    sa.column("name", sa.String),
    sa.column("color", sa.String),
    sa.column("position", sa.Integer),
    sa.column("allowed_shape_types", JSONDocument),
    sa.column("skeleton_edges", JSONDocument),
    sa.column("created_at", UTCDateTime()),
    sa.column("updated_at", UTCDateTime()),
)
_shapes = sa.table(
    "shapes",
    sa.column("id", GUID()),
    sa.column("label_id", GUID()),
    sa.column("shape_type", sa.String),
    sa.column("elements", JSONDocument),
)
_tracks = sa.table(
    "tracks",
    sa.column("id", GUID()),
    sa.column("label_id", GUID()),
    sa.column("shape_type", sa.String),
)
_track_shapes = sa.table(
    "track_shapes",
    sa.column("id", GUID()),
    sa.column("track_id", GUID()),
    sa.column("elements", JSONDocument),
)


def _resolve(elements: list[dict[str, Any]], joints: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """One stored joint per declared joint, in declared order (see the module docstring)."""
    ids = {joint_id for joint_id, _ in joints}
    by_id = {
        str(element["label_id"]): element
        for element in elements
        if element.get("label_id") and str(element["label_id"]) in ids
    }
    by_name = {str(element["name"]): element for element in elements if element.get("name")}
    resolved = []
    for index, (joint_id, joint_name) in enumerate(joints):
        element = by_id.get(joint_id) or by_name.get(joint_name)
        if element is None and not by_id and not by_name:
            element = elements[index] if index < len(elements) else None
        points = list((element or {}).get("points") or [])
        resolved.append(
            {
                "label_id": joint_id,
                "points": [float(points[0]), float(points[1])] if len(points) >= 2 else [0.0, 0.0],
                "occluded": bool(element.get("occluded", False)) if element else False,
                "outside": bool(element.get("outside", False)) if element else True,
            }
        )
    return resolved


def _names(rows: list[list[dict[str, Any]]]) -> list[str]:
    """The joints a jointless label's skeletons name, in first-seen order."""
    names: list[str] = []
    widest = 0
    for elements in rows:
        widest = max(widest, len(elements))
        for element in elements:
            name = str(element.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names or [f"joint {index + 1}" for index in range(widest)]


def upgrade() -> None:
    bind = op.get_bind()

    skeleton_shapes = [
        (row.id, str(row.label_id), list(row.elements or []), "shape")
        for row in bind.execute(
            sa.select(_shapes.c.id, _shapes.c.label_id, _shapes.c.elements).where(
                _shapes.c.shape_type == "skeleton"
            )
        )
    ]
    skeleton_keyframes = [
        (row.id, str(row.label_id), list(row.elements or []), "track_shape")
        for row in bind.execute(
            sa.select(_track_shapes.c.id, _tracks.c.label_id, _track_shapes.c.elements)
            .select_from(_track_shapes.join(_tracks, _track_shapes.c.track_id == _tracks.c.id))
            .where(_tracks.c.shape_type == "skeleton")
        )
    ]
    rows = skeleton_shapes + skeleton_keyframes
    if not rows:
        return

    label_ids = {label_id for _, label_id, _, _ in rows}
    parents = {
        str(row.id): row
        for row in bind.execute(
            sa.select(_labels.c.id, _labels.c.project_id, _labels.c.color).where(
                _labels.c.id.in_([uuid.UUID(label_id) for label_id in label_ids])
            )
        )
    }
    joints: dict[str, list[tuple[str, str]]] = {label_id: [] for label_id in label_ids}
    for row in bind.execute(
        sa.select(_labels.c.id, _labels.c.parent_id, _labels.c.name)
        .where(_labels.c.parent_id.in_([uuid.UUID(label_id) for label_id in label_ids]))
        .order_by(_labels.c.position, _labels.c.name)
    ):
        joints[str(row.parent_id)].append((str(row.id), row.name))

    # A label with no joints gets the ones its skeletons name.
    now = datetime.now(UTC)
    for label_id, declared in joints.items():
        parent = parents.get(label_id)
        if declared or parent is None:
            continue
        named = _names([elements for _, owner, elements, _ in rows if owner == label_id])
        for position, name in enumerate(named):
            joint_id = uuid.uuid4()
            bind.execute(
                _labels.insert().values(
                    id=joint_id,
                    project_id=parent.project_id,
                    parent_id=uuid.UUID(label_id),
                    name=name,
                    color=parent.color,
                    position=position,
                    allowed_shape_types=[],
                    skeleton_edges=[],
                    created_at=now,
                    updated_at=now,
                )
            )
            declared.append((str(joint_id), name))

    for row_id, label_id, elements, kind in rows:
        declared = joints.get(label_id) or []
        if not declared:
            continue  # its label is gone; nothing to resolve against
        repaired = _resolve(elements, declared)
        if repaired == elements:
            continue
        table = _shapes if kind == "shape" else _track_shapes
        bind.execute(table.update().where(table.c.id == row_id).values(elements=repaired))


def downgrade() -> None:
    # Nothing to undo: a repaired joint is valid under the previous schema as well, and the
    # rows as they were are not recoverable from the rows as they are.
    pass
