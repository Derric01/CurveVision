"""Record which version of a job a quality report scored

A report is a statement about annotations as they were at one moment, and the
annotations keep moving. Storing the job's ``annotation_version`` alongside the
score is what lets a reader tell a current report from a stale one.

Nullable: reports written before this column existed do not know which version
they scored, and inventing one for them would make a stale report look current —
exactly the failure the column exists to prevent.

Revision ID: 4b1c7de9a20f
Revises: cdf316f08ce2
Create Date: 2026-09-13 03:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b1c7de9a20f"
down_revision: str | None = "cdf316f08ce2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("quality_reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("annotation_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quality_reports", schema=None) as batch_op:
        batch_op.drop_column("annotation_version")
