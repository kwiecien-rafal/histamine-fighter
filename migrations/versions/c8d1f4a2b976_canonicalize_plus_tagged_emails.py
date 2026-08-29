"""canonicalize plus tagged emails

Revision ID: c8d1f4a2b976
Revises: b3e7a9c14d52
Create Date: 2026-07-05 12:00:00.000000

``normalize_email`` now strips plus-tags (``gerald+news@`` -> ``gerald@``) so one
inbox cannot mint unlimited accounts, each with its own shared-tier quota. Rows
stored before that rule must match the new lookup form, or their owners would be
locked out (lookups would miss) and re-register as duplicates.

A unique-email collision (``a+x@d`` and ``a@d`` both already registered) aborts
the migration loudly on the constraint — deliberate: which of the two accounts
survives is an operator decision, not something a migration may guess. Resolve
the duplicate by hand and rerun. No such row exists at the time of writing.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d1f4a2b976"
down_revision: str | Sequence[str] | None = "b3e7a9c14d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # split_part is safe on tag-less rows (returns the whole local part) and the
    # WHERE keeps the rewrite to rows that actually change. Local parts that are
    # nothing but a tag ("+x@d") are left alone, mirroring normalize_email.
    op.execute(
        sa.text(
            "UPDATE users"
            " SET email = split_part(email, '+', 1) || '@' || split_part(email, '@', 2)"
            " WHERE email LIKE '%+%@%' AND split_part(email, '+', 1) <> ''"
        )
    )


def downgrade() -> None:
    # The plus-tags are gone; there is nothing to restore. Accounts stay valid
    # under the old code too, since the base address was always the same inbox.
    pass
