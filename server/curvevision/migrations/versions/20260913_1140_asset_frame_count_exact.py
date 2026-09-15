"""Record whether an asset's frame count was counted or estimated

A video task is created with an estimated frame count, because counting means
decoding the whole file and that cannot happen inside an upload request. A
background job normally replaces the estimate within seconds, but it can decline
(the task already carries annotations, so its frame ranges are not the job's to
move) or fail (a file that moved, a codec this build cannot decode). Until now
nothing recorded which of those happened, so a count that overstates the media --
offering frames that do not exist -- was indistinguishable from a correct one.

Backfill: image assets are set exact, because an image contributes exactly one
frame by definition and no decode establishes that. Video assets are left False,
which understates what is known for the ones already counted before this column
existed. That is the safe direction: the consequence is a warning on a task that
turns out to be fine, and clearing it costs one idempotent re-probe. The opposite
default would silence the warning on exactly the tasks it exists for.

Revision ID: 7a3e1c65b904
Revises: 4b1c7de9a20f
Create Date: 2026-09-13 11:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7a3e1c65b904"
down_revision: str | None = "4b1c7de9a20f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Lightweight table stubs rather than the ORM models: a migration has to keep working when
# the models move on, and only these three columns matter here.
_assets = sa.table(
    "assets",
    sa.column("blob_id", sa.Uuid),
    sa.column("frame_count_exact", sa.Boolean),
)
_blobs = sa.table(
    "media_blobs",
    sa.column("id", sa.Uuid),
    sa.column("kind", sa.String),
)


def upgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        # `server_default` is what makes this addable to a populated table; it is kept
        # afterwards so a hand-written INSERT stays valid. The ORM sends the value
        # explicitly on every insert, so the two never disagree.
        batch_op.add_column(
            sa.Column(
                "frame_count_exact",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    op.execute(
        _assets.update()
        .where(
            _assets.c.blob_id.in_(sa.select(_blobs.c.id).where(_blobs.c.kind != "video")),
        )
        .values(frame_count_exact=sa.true())
    )


def downgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_column("frame_count_exact")
