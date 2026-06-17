"""index curated meals approval status

Revision ID: b8d3f1e6a942
Revises: c4d8f1a36b02
Create Date: 2026-06-17 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d3f1e6a942"
down_revision: str | Sequence[str] | None = "c4d8f1a36b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every retrieval filters on approval_status == 'approved'; index it so the
    # filter does not seq-scan the pool as it grows. Name matches the model's
    # naming convention so autogenerate (alembic check) sees no drift.
    op.create_index(
        op.f("ix_curated_meals_approval_status"),
        "curated_meals",
        ["approval_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_curated_meals_approval_status"), table_name="curated_meals")
