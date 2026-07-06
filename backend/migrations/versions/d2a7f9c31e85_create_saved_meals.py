"""create saved meals

Revision ID: d2a7f9c31e85
Revises: c8d1f4a2b976
Create Date: 2026-07-06 10:30:00.000000

Per-user snapshots of saved meals (curated pool, daily board, or a dish-lookup
assessment). First table referencing ``users``: the FK cascades so GDPR account
deletion cannot leave orphaned personal data behind.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d2a7f9c31e85"
down_revision: str | Sequence[str] | None = "c8d1f4a2b976"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_meals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_key", sa.String(), nullable=False),
        sa.Column("meal_type", sa.String(length=16), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("ingredients", postgresql.JSONB(), nullable=False),
        sa.Column("recipe", postgresql.JSONB(), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "cautioned_ingredients",
            postgresql.JSONB(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "source IN ('curated', 'daily', 'lookup')",
            name=op.f("ck_saved_meals_save_source"),
        ),
        sa.CheckConstraint(
            "meal_type IN ('breakfast', 'lunch', 'dinner', 'snack')",
            name=op.f("ck_saved_meals_meal_type"),
        ),
        sa.CheckConstraint(
            "verdict IN ('safe', 'depends', 'avoid')",
            name=op.f("ck_saved_meals_safety_level"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_saved_meals_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_meals")),
        sa.UniqueConstraint("user_id", "source", "source_key", name="uq_saved_meals_user_source"),
    )
    op.create_index(op.f("ix_saved_meals_user_id"), "saved_meals", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_saved_meals_user_id"), table_name="saved_meals")
    op.drop_table("saved_meals")
