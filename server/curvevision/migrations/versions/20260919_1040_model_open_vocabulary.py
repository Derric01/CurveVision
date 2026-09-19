"""Record whether a registered model takes its classes as text

A detector with a fixed head can only find what it was trained on, and
``output_labels`` is the whole of that. YOLO-World, Grounding DINO and OWL-ViT
are not like that: they take the class names at inference time, so their label
list is a default rather than a boundary.

Until now the contract could not express the difference, so CurveVision could
not ask a model to find "forklift" unless somebody had trained a forklift
detector. The column is what lets the two be told apart, and the two fail in
opposite directions: sending classes to a fixed-head model does nothing and the
caller cannot tell, while sending none to an open-vocabulary one returns nothing
and looks like a broken model.

A first-class column rather than a key in ``config``, which is documented as
provider *connection* settings — endpoint, headers, timeouts. Whether a model has
a fixed label space is a property of the model.

Default False: every model registered before this one has a fixed label space as
far as anything here knows, and assuming otherwise would start sending prompts
to servers that will drop them silently.

Revision ID: 9c2f4d81ae63
Revises: 7a3e1c65b904
Create Date: 2026-09-19 10:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c2f4d81ae63"
down_revision: str | None = "7a3e1c65b904"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_registrations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "open_vocabulary",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("model_registrations", schema=None) as batch_op:
        batch_op.drop_column("open_vocabulary")
