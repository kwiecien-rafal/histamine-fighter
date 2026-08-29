"""ORM model for the daily board: one composed meal per slot per calendar date.

Each row is one meal type's suggestion for a date: the composer's recorded output
(content + reasoning trace), the time it unlocks, and its approval state. The public
board reads these by date and reveals them once ``reveal_at`` has passed and an admin
has approved; the recorded trace backs a per-meal "how it was composed" replay. Unlike
curated_meals these are read by ``(suggestion_date, meal_type)``, not by similarity, so
there is no embedding column.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Enum, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_values
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.enums import ApprovalStatus, MealType


class DailySuggestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One day's suggestion for a single meal type, pending review or revealed."""

    __tablename__ = "daily_suggestions"

    suggestion_date: Mapped[date] = mapped_column(Date)
    meal_type: Mapped[MealType] = mapped_column(
        Enum(
            MealType,
            native_enum=False,
            length=16,
            name="meal_type",
            create_constraint=True,
            values_callable=enum_values,
        )
    )
    # The composed meal (name, description, ingredients, recipe, tags) as one blob;
    # the board is read whole, so the content does not need its own columns.
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # The producing model, surfaced on the board's transparency badge.
    model: Mapped[str]
    # Token usage of the composition (an LLMUsage blob); the board totals these for
    # its cost badge. Null for rows composed before this was recorded.
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    reasoning_trace: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'")
    )
    # When the board unlocks; the reveal is a clock check, never live computation.
    reveal_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus,
            native_enum=False,
            length=16,
            name="approval_status",
            create_constraint=True,
            values_callable=enum_values,
        ),
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    approved_by: Mapped[str | None] = mapped_column(default=None)

    __table_args__ = (
        UniqueConstraint(
            "suggestion_date", "meal_type", name="uq_daily_suggestions_date_meal_type"
        ),
    )

    def __repr__(self) -> str:
        return f"<DailySuggestion {self.suggestion_date} {self.meal_type}: {self.approval_status}>"
