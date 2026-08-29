"""rename admin_users to users, add role and is_active

Revision ID: f7b2c4e91a35
Revises: a3f9c1e7b204
Create Date: 2026-06-22 13:00:00.000000

Renames the account table and makes it role-ready. The ordering is
security-minded: ``role`` is added nullable with no blanket default, every
existing row is backfilled to ``admin`` (they are today's operators), and only
then does the column become NOT NULL with a ``user`` server default. So existing
admins keep their access while any future raw insert defaults to least privilege.
``is_active`` lands as a NOT NULL kill switch defaulting to true.

Existing sessions do not survive the upgrade: the access token now carries the
user id as its subject instead of the email, so any token minted beforehand fails
the auth gate and every operator signs in again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7b2c4e91a35"
down_revision: str | Sequence[str] | None = "a3f9c1e7b204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("admin_users", "users")
    # Renaming the table leaves the index and PK constraint under their old names.
    # Rename them so the schema reads cleanly. RENAME CONSTRAINT on a PK also
    # renames its backing index.
    op.execute('ALTER INDEX "ix_admin_users_email" RENAME TO "ix_users_email"')
    op.execute('ALTER TABLE "users" RENAME CONSTRAINT "pk_admin_users" TO "pk_users"')

    op.add_column("users", sa.Column("role", sa.String(length=16), nullable=True))
    op.execute("UPDATE users SET role = 'admin'")
    op.alter_column("users", "role", nullable=False, server_default="user")
    op.create_check_constraint(op.f("ck_users_role"), "users", "role IN ('user', 'admin')")

    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_active")
    # Dropping the column drops its CHECK constraint with it.
    op.drop_column("users", "role")
    op.execute('ALTER TABLE "users" RENAME CONSTRAINT "pk_users" TO "pk_admin_users"')
    op.execute('ALTER INDEX "ix_users_email" RENAME TO "ix_admin_users_email"')
    op.rename_table("users", "admin_users")
