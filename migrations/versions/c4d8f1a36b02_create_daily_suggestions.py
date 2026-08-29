"""create daily suggestions

Revision ID: c4d8f1a36b02
Revises: d8c3b1a4f762
Create Date: 2026-06-16 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c4d8f1a36b02"
down_revision: str | Sequence[str] | None = "d8c3b1a4f762"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("suggestion_date", sa.Date(), nullable=False),
        sa.Column("meal_type", sa.String(length=16), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "reasoning_trace",
            postgresql.JSONB(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("reveal_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "approval_status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
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
            "meal_type IN ('breakfast', 'lunch', 'dinner', 'snack')",
            name=op.f("ck_daily_suggestions_meal_type"),
        ),
        sa.CheckConstraint(
            "approval_status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_daily_suggestions_approval_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_suggestions")),
        # One suggestion per slot per day: the board is keyed on (date, meal_type).
        sa.UniqueConstraint(
            "suggestion_date", "meal_type", name="uq_daily_suggestions_date_meal_type"
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_suggestions")
