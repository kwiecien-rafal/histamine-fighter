"""rewrite composer trace kind swap to options

Revision ID: c4e8b1a7f309
Revises: f1b6a3d9c274
Create Date: 2026-06-17 17:00:00.000000

The composer's ``FindSafeIngredients`` step used to author trace events with
``kind="swap"``; that kind was renamed to ``options``. Any meal composed before
the rename has ``swap`` events stored in its reasoning_trace JSONB, which no longer
validates against TraceEvent on read. Rewrite them so the board and admin reads do
not break on old rows. New ``options`` events are indistinguishable from these, so
the downgrade cannot be reversed safely and is a no-op.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8b1a7f309"
down_revision: str | Sequence[str] | None = "f1b6a3d9c274"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REWRITE = """
    UPDATE {table}
    SET reasoning_trace = (
        SELECT jsonb_agg(
            CASE WHEN event->>'kind' = 'swap'
                 THEN jsonb_set(event, '{{kind}}', '"options"')
                 ELSE event
            END
        )
        FROM jsonb_array_elements(reasoning_trace) AS event
    )
    WHERE reasoning_trace @> '[{{"kind": "swap"}}]'
"""


def upgrade() -> None:
    for table in ("curated_meals", "daily_suggestions"):
        op.execute(sa.text(_REWRITE.format(table=table)))


def downgrade() -> None:
    # Renamed events are now identical to legitimately new ``options`` events, so
    # there is nothing safe to reverse.
    pass
