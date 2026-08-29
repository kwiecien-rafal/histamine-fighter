"""flag category umbrella rows

Revision ID: c9d4e71b2a56
Revises: f4a8d27c3b51
Create Date: 2026-06-11 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d4e71b2a56"
down_revision: str | Sequence[str] | None = "f4a8d27c3b51"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No backfill: the seed file is the data path, and re-running the seed after
    # this migration sets the flag on the curated umbrella rows.
    op.add_column(
        "histamine_ingredients",
        sa.Column("is_category", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("histamine_ingredients", "is_category")
