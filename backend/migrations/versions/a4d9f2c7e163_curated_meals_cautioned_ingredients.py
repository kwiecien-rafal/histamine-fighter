"""curated meals cautioned ingredients

Revision ID: a4d9f2c7e163
Revises: d6f4b8c12e07
Create Date: 2026-07-03 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a4d9f2c7e163"
down_revision: str | Sequence[str] | None = "d6f4b8c12e07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "curated_meals",
        sa.Column(
            "cautioned_ingredients",
            postgresql.JSONB(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("curated_meals", "cautioned_ingredients")
