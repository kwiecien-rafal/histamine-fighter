"""create learn query cache

Revision ID: f4a8d27c3b51
Revises: e2b9c47a1f08
Create Date: 2026-06-10 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "f4a8d27c3b51"
down_revision: str | Sequence[str] | None = "e2b9c47a1f08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learn_query_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question_key", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("response", JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_learn_query_cache"),
        sa.UniqueConstraint(
            "question_key", "model", name="uq_learn_query_cache_question_key_model"
        ),
    )


def downgrade() -> None:
    op.drop_table("learn_query_cache")
