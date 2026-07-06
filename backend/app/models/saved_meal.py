"""ORM model for per-user saved meals.

Each row is one user's snapshot of a meal they saved: from the curated pool,
the daily board, or a dish-lookup assessment. A snapshot, not a reference — the
user may edit their copy, the source rows change or get pruned independently,
and a lookup result has no durable row to point at. ``source`` + ``source_key``
keep provenance and dedupe repeat saves; ``edited_at`` marks a user-modified
copy, which the frontend must never present as index-verified.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_values
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.enums import MealType, SafetyLevel, SaveSource


class SavedMeal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One user's saved, editable copy of a meal they saved."""

    __tablename__ = "saved_meals"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[SaveSource] = mapped_column(
        Enum(
            SaveSource,
            native_enum=False,
            length=16,
            name="save_source",
            create_constraint=True,
            values_callable=enum_values,
        )
    )
    # Curated/daily: the source row's UUID as text. Lookup: the normalized dish
    # name, derived server-side so a client cannot mint colliding or junk keys.
    source_key: Mapped[str]
    # Null for lookup saves: an assessed dish has no meal slot.
    meal_type: Mapped[MealType | None] = mapped_column(
        Enum(
            MealType,
            native_enum=False,
            length=16,
            name="meal_type",
            create_constraint=True,
            values_callable=enum_values,
        ),
        default=None,
    )
    name: Mapped[str]
    description: Mapped[str] = mapped_column(Text)
    ingredients: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    recipe: Mapped[list[str] | None] = mapped_column(JSONB, default=None)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'")
    )
    cautioned_ingredients: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'")
    )
    # The producing model, kept for the transparency badge on the profile.
    model: Mapped[str]
    # The code-computed verdict a lookup save was assessed at; null for pool copies.
    verdict: Mapped[SafetyLevel | None] = mapped_column(
        Enum(
            SafetyLevel,
            native_enum=False,
            length=16,
            name="safety_level",
            create_constraint=True,
            values_callable=enum_values,
        ),
        default=None,
    )
    # When the user first changed their copy. updated_at bumps on any ORM write,
    # so this is the flag that drops the verified badge, not the mixin column.
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_key", name="uq_saved_meals_user_source"),
    )

    def __repr__(self) -> str:
        return f"<SavedMeal {self.name!r} ({self.source}) user={self.user_id}>"
