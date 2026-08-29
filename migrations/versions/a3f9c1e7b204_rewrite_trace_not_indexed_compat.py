"""rewrite composer trace compatibility 'not indexed' to 'not_indexed'

Revision ID: a3f9c1e7b204
Revises: b9e1d7c3a402
Create Date: 2026-06-22 12:00:00.000000

A composer ``check`` step used to store its reading in ``compatibility`` as the
free string ``not indexed``; the field is now the ``TraceReading`` enum, whose
value is ``not_indexed``. Any meal composed before that change has the spaced
string in its reasoning_trace JSONB, which no longer validates against TraceEvent
on read, so the board and admin reads 500 on old rows. Rewrite them. New rows
already carry ``not_indexed``, so this is a no-op for them and the downgrade
cannot be reversed safely.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f9c1e7b204"
down_revision: str | Sequence[str] | None = "b9e1d7c3a402"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REWRITE = """
    UPDATE {table}
    SET reasoning_trace = (
        SELECT jsonb_agg(
            CASE WHEN event->>'compatibility' = 'not indexed'
                 THEN jsonb_set(event, '{{compatibility}}', '"not_indexed"')
                 ELSE event
            END
        )
        FROM jsonb_array_elements(reasoning_trace) AS event
    )
    WHERE reasoning_trace @> '[{{"compatibility": "not indexed"}}]'
"""


def upgrade() -> None:
    for table in ("curated_meals", "daily_suggestions"):
        op.execute(sa.text(_REWRITE.format(table=table)))


def downgrade() -> None:
    # Rewritten readings are now identical to legitimately new ``not_indexed``
    # values, so there is nothing safe to reverse.
    pass
