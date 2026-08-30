"""create lookup rewrite cache

Revision ID: b3d5e91f7c02
Revises: a91c4f7d20b3
Create Date: 2026-08-30 11:20:00.000000

The third dish-lookup cache tier: a rewritten, index-cleared version of a dish,
keyed by the canonical dish key plus a hash of the ingredient set it was asked
about. Only the outcome that cost model calls is stored. ``grounding_hash``
covers both the readings the rewrite was derived from and the readings that
cleared the list it produced, so a row stops being served the moment the index
no longer supports the dish it hands back. ``expires_at`` is indexed because the
store-time GC delete filters on it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "b3d5e91f7c02"
down_revision: str | Sequence[str] | None = "a91c4f7d20b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lookup_rewrite_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dish_key", sa.String(), nullable=False),
        sa.Column("ingredients_hash", sa.String(), nullable=False),
        sa.Column("response", JSONB(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("grounding_hash", sa.String(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
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
        sa.CheckConstraint(
            "verdict IN ('safe', 'depends', 'avoid')",
            name=op.f("ck_lookup_rewrite_cache_safety_level"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lookup_rewrite_cache")),
        sa.UniqueConstraint(
            "dish_key",
            "ingredients_hash",
            name="uq_lookup_rewrite_cache_dish_ingredients",
        ),
    )
    op.create_index(
        op.f("ix_lookup_rewrite_cache_expires_at"), "lookup_rewrite_cache", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("lookup_rewrite_cache")
