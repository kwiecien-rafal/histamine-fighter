"""public users and quotas

Revision ID: b3e7a9c14d52
Revises: a4d9f2c7e163
Create Date: 2026-07-03 12:00:00.000000

Opens the account table to passwordless public users and adds the tables the
shared LLM tier needs: ``magic_link_tokens`` for single-use email logins and
``usage_counters`` for the per-user / per-IP / global daily quotas.
``password_hash`` becomes nullable because public accounts never have one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e7a9c14d52"
down_revision: str | Sequence[str] | None = "a4d9f2c7e163"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("users", "password_hash", existing_type=sa.String(), nullable=True)
    op.add_column("users", sa.Column("created_from_ip", sa.String(), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "magic_link_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_from_ip", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_magic_link_tokens")),
    )
    op.create_index(
        op.f("ix_magic_link_tokens_email"), "magic_link_tokens", ["email"], unique=False
    )

    op.create_table(
        "usage_counters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_counters")),
        sa.UniqueConstraint("scope", "key", "date", name=op.f("uq_usage_counters_scope")),
    )


def downgrade() -> None:
    op.drop_table("usage_counters")
    op.drop_index(op.f("ix_magic_link_tokens_email"), table_name="magic_link_tokens")
    op.drop_table("magic_link_tokens")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "created_from_ip")
    # Passwordless (public) accounts cannot satisfy NOT NULL again; they only make
    # sense in the world this migration created, so the downgrade removes them.
    op.execute("DELETE FROM users WHERE password_hash IS NULL")
    op.alter_column("users", "password_hash", existing_type=sa.String(), nullable=False)
