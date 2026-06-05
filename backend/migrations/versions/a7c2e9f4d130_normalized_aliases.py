"""normalized aliases lookup key

Revision ID: a7c2e9f4d130
Revises: d3f1c8a7b620
Create Date: 2026-06-05 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7c2e9f4d130"
down_revision: str | Sequence[str] | None = "d3f1c8a7b620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Store a normalized form of each alias so lookups match by array membership
    # instead of normalizing every row in SQL at query time.
    op.add_column(
        "histamine_ingredients",
        sa.Column(
            "normalized_aliases",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    # One-time backfill of existing rows. This mirrors normalize_ingredient_name
    # (lowercase, trim, collapse whitespace); it is a frozen historical transform,
    # not live logic, so it does not reintroduce a second normalization code path.
    op.execute(
        r"""
        UPDATE histamine_ingredients
        SET normalized_aliases = ARRAY(
            SELECT regexp_replace(lower(btrim(alias)), '\s+', ' ', 'g')
            FROM unnest(aliases) AS alias
        )
        WHERE cardinality(aliases) > 0
        """
    )


def downgrade() -> None:
    op.drop_column("histamine_ingredients", "normalized_aliases")
