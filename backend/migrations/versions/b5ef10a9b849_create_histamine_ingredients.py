"""create histamine ingredients

Revision ID: b5ef10a9b849
Revises:
Create Date: 2026-06-01 00:42:48.968927

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b5ef10a9b849"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fuzzy ingredient matching relies on trigram similarity.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "histamine_ingredients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False),
        # SIGHI compatibility scale (0-3). NULL mirrors SIGHI's "-" and "?".
        sa.Column("compatibility", sa.String(length=24), nullable=True),
        # SIGHI mechanism flags (H!, H, A, L, B). Element values validated in the app.
        sa.Column(
            "mechanisms",
            postgresql.ARRAY(sa.String(length=16)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
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
            "compatibility IN ('well_tolerated', 'moderately_compatible', "
            "'incompatible', 'poorly_tolerated')",
            name=op.f("ck_histamine_ingredients_compatibility"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_histamine_ingredients")),
        sa.UniqueConstraint(
            "normalized_name", name=op.f("uq_histamine_ingredients_normalized_name")
        ),
    )
    op.create_index(
        "ix_histamine_ingredients_normalized_name_trgm",
        "histamine_ingredients",
        ["normalized_name"],
        postgresql_using="gin",
        postgresql_ops={"normalized_name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_histamine_ingredients_normalized_name_trgm",
        table_name="histamine_ingredients",
    )
    op.drop_table("histamine_ingredients")
    # pg_trgm is left installed in case other objects rely on it.
