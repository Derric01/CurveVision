"""Index jobs by reviewer and state, the way they are already indexed by assignee

``ix_job_assignee_state`` has existed since the initial schema because
``GET /jobs?mine=true`` is the query an annotator's landing page runs on every
visit. A reviewer now has the same landing page and the same query with one
column changed -- ``reviewer_id`` instead of ``assignee_id``, usually alongside
``state=submitted`` -- and no index behind it.

Without this the review queue scans every job the caller can see and filters in
memory, which is invisible on a laptop with two jobs and is not invisible on an
installation with a year of them.

Revision ID: b5e0c72f18d4
Revises: 9c2f4d81ae63
Create Date: 2026-09-21 07:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b5e0c72f18d4"
down_revision: str | None = "9c2f4d81ae63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index("ix_job_reviewer_state", ["reviewer_id", "state"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_job_reviewer_state")
