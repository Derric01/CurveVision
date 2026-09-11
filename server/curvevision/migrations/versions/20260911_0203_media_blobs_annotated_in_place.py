"""Media annotated in place: nullable storage_key, add source_path

The desktop application annotates images where they already sit on disk, so a blob
may have a filesystem path on this machine instead of an object-storage key. Exactly
one of the two is set.

Revision ID: cdf316f08ce2
Revises: 95b306a6e3a8
Create Date: 2026-09-11 02:03:41.866146
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text  # noqa: F401 - referenced by rendered JSONB(astext_type=...)

import curvevision.core.types  # noqa: F401 - referenced by rendered GUID() columns


revision: str = "cdf316f08ce2"
down_revision: str | None = "95b306a6e3a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_blobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_path", sa.String(length=4000), nullable=True))
        batch_op.alter_column("storage_key", existing_type=sa.VARCHAR(length=500), nullable=True)



def downgrade() -> None:
    with op.batch_alter_table("media_blobs", schema=None) as batch_op:
        # Any blob without a storage key was annotated in place and cannot be
        # represented by the old schema; there is nothing to copy it from.
        batch_op.drop_column("source_path")
        batch_op.alter_column("storage_key", existing_type=sa.VARCHAR(length=500), nullable=False)

