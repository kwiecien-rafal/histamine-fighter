"""curated meals unverified ingredients

Revision ID: e7a2c91f4d38
Revises: b8d3f1e6a942
Create Date: 2026-06-17 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e7a2c91f4d38"
down_revision: str | Sequence[str] | None = "b8d3f1e6a942"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "curated_meals",
        sa.Column(
            "unverified_ingredients",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("curated_meals", "unverified_ingredients")
