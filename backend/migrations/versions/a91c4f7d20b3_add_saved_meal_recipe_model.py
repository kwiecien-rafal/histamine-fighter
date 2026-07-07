"""add saved meal recipe model

Revision ID: a91c4f7d20b3
Revises: e6b2d8f41a97
Create Date: 2026-07-07 12:00:00.000000

Provenance for lazily generated recipes: the model that wrote the steps, kept
apart from ``model`` (the save's original producer) so the transparency badge
stays truthful when the two differ.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a91c4f7d20b3"
down_revision: str | Sequence[str] | None = "e6b2d8f41a97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("saved_meals", sa.Column("recipe_model", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("saved_meals", "recipe_model")
