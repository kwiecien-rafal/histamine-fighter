"""ORM model for application accounts.

One row per account that can sign in. Today every account is an admin created by
``python -m app.scripts.create_admin`` (CLAUDE section 10); there is no
self-registration endpoint yet. The model is role-ready so public users can be
added later without reshaping auth: ``role`` decides what an account may do and is
read from the database on every request, never trusted from the token. The
password is stored as a bcrypt hash, never in plaintext, and the email is
normalized so one operator cannot split into two accounts.
"""

from sqlalchemy import Enum, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base, enum_values
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.enums import Role


def normalize_email(email: str) -> str:
    """Return the stored/lookup form of an email: trimmed and lowercased.

    Login and account creation both normalize through here so a stray capital or
    surrounding space can never split one operator into two accounts.
    """
    return email.strip().lower()


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An account allowed to sign in; ``role`` decides what it may do."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str]
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

    @validates("email")
    def _normalize_email(self, _key: str, email: str) -> str:
        return normalize_email(email)

    def __repr__(self) -> str:
        return f"<User {self.email!r} ({self.role})>"
