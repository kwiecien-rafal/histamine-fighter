"""ORM model for magic-link login tokens.

One row per magic-link email sent. The emailed link carries a signed JWT whose
``jti`` is this row's id, so tampering dies at signature check before any DB
lookup; the row itself enforces what a signature cannot: single use
(``consumed_at``) and the guess cap on the 6-digit code (``attempts``). The code
is stored bcrypt-hashed, like a password, because it is one.
"""

from datetime import datetime

from sqlalchemy import DateTime, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class MagicLinkToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A pending passwordless login; the row id is the token's ``jti``."""

    __tablename__ = "magic_link_tokens"

    email: Mapped[str] = mapped_column(index=True)
    code_hash: Mapped[str]
    # Failed 6-digit-code entries against this row. Incremented before checking,
    # so a guess past the cap is refused even if it happens to be correct.
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    created_from_ip: Mapped[str | None]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<MagicLinkToken {self.email!r} consumed={self.consumed_at is not None}>"
