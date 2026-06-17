"""add admin token_version

Revision ID: b9e1d7c3a402
Revises: c4e8b1a7f309
Create Date: 2026-06-17 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9e1d7c3a402"
down_revision: str | Sequence[str] | None = "c4e8b1a7f309"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default backfills existing rows; new rows get the value from the ORM.
    op.add_column(
        "admin_users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("admin_users", "token_version")
