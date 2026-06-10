"""ORM model for cached Learn-hub answers.

The corpus is static between seed runs and the questions repeat, so a grounded
answer is worth keeping: one row per (normalized question, model), holding the
serialized ``LearnResponse`` and a TTL. The seed script clears the table when it
rebuilds the corpus, so a cached answer never outlives the knowledge it cites.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class LearnQueryCache(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One cached, grounded answer for a normalized question and model."""

    __tablename__ = "learn_query_cache"

    question_key: Mapped[str]
    model: Mapped[str]
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("question_key", "model", name="uq_learn_query_cache_question_key_model"),
    )

    def __repr__(self) -> str:
        return f"<LearnQueryCache {self.question_key!r} ({self.model})>"
