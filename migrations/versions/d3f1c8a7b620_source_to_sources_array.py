"""source to sources array

Revision ID: d3f1c8a7b620
Revises: b5ef10a9b849
Create Date: 2026-06-02 09:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d3f1c8a7b620"
down_revision: str | Sequence[str] | None = "b5ef10a9b849"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A rating may draw on several references, so widen the single source column
    # into an array. Existing values become one-element arrays.
    op.alter_column("histamine_ingredients", "source", new_column_name="sources")
    op.alter_column(
        "histamine_ingredients",
        "sources",
        type_=postgresql.ARRAY(sa.String()),
        existing_type=sa.String(),
        existing_nullable=False,
        postgresql_using="ARRAY[sources]",
    )


def downgrade() -> None:
    op.alter_column(
        "histamine_ingredients",
        "sources",
        type_=sa.String(),
        existing_type=postgresql.ARRAY(sa.String()),
        existing_nullable=False,
        postgresql_using="array_to_string(sources, '; ')",
    )
    op.alter_column("histamine_ingredients", "sources", new_column_name="source")
