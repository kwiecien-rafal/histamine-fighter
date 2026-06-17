"""ORM model for the admin accounts that approve curated meals.

One row per operator who can sign in to the admin gate. Accounts are created
only by ``python -m app.scripts.create_admin`` (CLAUDE section 10); there is no
self-registration endpoint. The password is stored as a bcrypt hash, never in
plaintext, and the email is the JWT subject and the audit actor stamped on an
approval.
"""

from sqlalchemy import text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


def normalize_email(email: str) -> str:
    """Return the stored/lookup form of an email: trimmed and lowercased.

    Login and account creation both normalize through here so a stray capital or
    surrounding space can never split one operator into two accounts.
    """
    return email.strip().lower()


class AdminUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An operator allowed to sign in and approve or reject composed meals."""

    __tablename__ = "admin_users"

    email: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str]
    # Bumped on every password reset. The access token carries the version it was
    # issued under, so a reset invalidates any token minted before it.
    token_version: Mapped[int] = mapped_column(default=1, server_default=text("1"))

    @validates("email")
    def _normalize_email(self, _key: str, email: str) -> str:
        return normalize_email(email)

    def __repr__(self) -> str:
        return f"<AdminUser {self.email!r}>"
