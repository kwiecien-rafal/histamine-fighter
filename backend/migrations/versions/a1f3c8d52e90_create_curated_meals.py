"""create curated meals

Revision ID: a1f3c8d52e90
Revises: c9d4e71b2a56
Create Date: 2026-06-16 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1f3c8d52e90"
down_revision: str | Sequence[str] | None = "c9d4e71b2a56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Matches app.embeddings.EMBEDDING_DIM at creation time. The stored dimension is
# frozen into the column, so a model change to a different dimension is a new
# migration, not an edit to this one.
_EMBEDDING_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "curated_meals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("meal_type", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("ingredients", postgresql.JSONB(), nullable=False),
        sa.Column("recipe", postgresql.JSONB(), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "reasoning_trace",
            postgresql.JSONB(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "approval_status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=False),
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
            name=op.f("ck_curated_meals_meal_type"),
        ),
        sa.CheckConstraint(
            "approval_status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_curated_meals_approval_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_curated_meals")),
    )
    # No ANN index. At a small, curated pool an exact cosine scan is both faster
    # and exact; add HNSW/IVFFlat only if the pool grows past ~10k meals.


def downgrade() -> None:
    op.drop_table("curated_meals")
    # The vector extension is left installed in case other objects rely on it.
