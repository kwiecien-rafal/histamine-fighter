"""create lookup caches

Revision ID: e6b2d8f41a97
Revises: d2a7f9c31e85
Create Date: 2026-07-06 19:40:00.000000

The two dish-lookup cache tiers: proposals keyed by the canonical dish key,
assessments keyed by dish key plus a hash of the confirmed ingredient set.
A cached assessment is only served while its grounding fingerprint still
matches the live index. ``expires_at`` is indexed on both tables because the
store-time GC delete filters on it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "e6b2d8f41a97"
down_revision: str | Sequence[str] | None = "d2a7f9c31e85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lookup_proposal_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dish_key", sa.String(), nullable=False),
        sa.Column("response", JSONB(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lookup_proposal_cache")),
        sa.UniqueConstraint("dish_key", name=op.f("uq_lookup_proposal_cache_dish_key")),
    )
    op.create_index(
        op.f("ix_lookup_proposal_cache_expires_at"), "lookup_proposal_cache", ["expires_at"]
    )
    op.create_table(
        "lookup_assessment_cache",
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
            name=op.f("ck_lookup_assessment_cache_safety_level"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lookup_assessment_cache")),
        sa.UniqueConstraint(
            "dish_key",
            "ingredients_hash",
            name="uq_lookup_assessment_cache_dish_ingredients",
        ),
    )
    op.create_index(
        op.f("ix_lookup_assessment_cache_expires_at"), "lookup_assessment_cache", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("lookup_assessment_cache")
    op.drop_table("lookup_proposal_cache")
