"""create generation settings

Revision ID: d6f4b8c12e07
Revises: f7b2c4e91a35
Create Date: 2026-06-24 10:00:00.000000

Single-row table holding the operator-set composer provider and model, honoured by
the admin triggers and the nightly cron. No key or base_url column by design: secrets
stay in the environment, so the database never holds one and a stored value can never
point the server at an arbitrary host. A unique always-true ``is_singleton`` column
pins the table to one row.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6f4b8c12e07"
down_revision: str | Sequence[str] | None = "f7b2c4e91a35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("composer_provider", sa.String(), nullable=True),
        sa.Column("composer_model", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("is_singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_generation_settings"),
        sa.UniqueConstraint("is_singleton", name="uq_generation_settings_singleton"),
    )


def downgrade() -> None:
    op.drop_table("generation_settings")
