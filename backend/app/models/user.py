"""ORM model for application accounts.

One row per account that can sign in: admins created by
``python -m app.scripts.create_admin`` (CLAUDE section 10) and public users who
arrive passwordless through magic link or OAuth (``password_hash`` is NULL for
them — no password ever exists to leak). ``role`` decides what an account may do
and is read from the database on every request, never trusted from the token.
Admin passwords are stored as bcrypt hashes, never in plaintext, and the email is
normalized so one person cannot split into two accounts.
"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base, enum_values
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.enums import Role


def normalize_email(email: str) -> str:
    """Return the stored/lookup form of an email: trimmed, lowercased, untagged.

    Login and account creation both normalize through here so a stray capital or
    surrounding space can never split one person into two accounts. The plus-tag
    is stripped too (``gerald+news@`` -> ``gerald@``): subaddresses land in the
    same inbox, so keeping them distinct would mint unlimited accounts — each
    with its own shared-tier quota — from one mailbox. Sign-in emails still go
    to the address as typed; only the stored identity collapses.
    """
    email = email.strip().lower()
    local, sep, domain = email.partition("@")
    untagged = local.split("+", 1)[0]
    # A local part that *starts* with "+" would strip to nothing; keep it whole.
    if not sep or not untagged:
        return email
    return f"{untagged}@{domain}"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An account allowed to sign in; ``role`` decides what it may do."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(unique=True, index=True)
    # NULL for public users: they sign in via magic link or OAuth only, and the
    # admin password login treats a NULL hash exactly like an unknown account.
    password_hash: Mapped[str | None]
    # Bumped on every password reset. The access token carries the version it was
    # issued under, so a reset invalidates any token minted before it.
    token_version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            native_enum=False,
            length=16,
            name="role",
            create_constraint=True,
            values_callable=enum_values,
        ),
        default=Role.USER,
        server_default=Role.USER.value,
    )
    # Soft-disable switch checked at the auth gate: a false value cuts an account
    # off without deleting the row, so the audit trail it is stamped on survives.
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # Where the account was created from, for the signup velocity cap and abuse
    # triage. NULL on accounts predating public signup and on CLI-created admins.
    created_from_ip: Mapped[str | None]
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @validates("email")
    def _normalize_email(self, _key: str, email: str) -> str:
        return normalize_email(email)

    def __repr__(self) -> str:
        return f"<User {self.email!r} ({self.role})>"
