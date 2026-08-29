"""record composer token usage on composed meals

Revision ID: f1b6a3d9c274
Revises: e7a2c91f4d38
Create Date: 2026-06-17 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1b6a3d9c274"
down_revision: str | Sequence[str] | None = "e7a2c91f4d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("curated_meals", sa.Column("usage", postgresql.JSONB(), nullable=True))
    op.add_column("daily_suggestions", sa.Column("usage", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("daily_suggestions", "usage")
    op.drop_column("curated_meals", "usage")
