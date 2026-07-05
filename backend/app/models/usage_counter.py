"""ORM model for daily usage counters.

One row per (scope, key, UTC day): a user's shared-tier calls, an IP's
shared-tier calls, the site-wide total, or an IP's signups. The unique
constraint makes the conditional upsert in QuotaService atomic, which is the
whole quota mechanism; slowapi stays the per-minute burst layer while these
rows are the per-day budget.
"""

from datetime import date

from sqlalchemy import Date, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UsageCounter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A per-day counter for one scope and key.

    Scopes are the ``QuotaScope`` literals owned by QuotaService: ``user``,
    ``ip``, ``global``, and ``signup_ip``.
    """

    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("scope", "key", "date"),)

    scope: Mapped[str]
    # The counted identity: a user UUID, an IP address, or "all" for the global row.
    key: Mapped[str]
    date: Mapped[date] = mapped_column(Date)
    count: Mapped[int] = mapped_column(default=0, server_default=text("0"))

    def __repr__(self) -> str:
        return f"<UsageCounter {self.scope}:{self.key} {self.date} = {self.count}>"
