"""ORM model for the curated, admin-approved meal pool.

Each row is a meal the ComposerAgent built forward from index-safe ingredients,
re-verified in code, and an admin approved. Retrieval (MealService) reads the
approved rows by vector similarity. The model never writes a safety verdict here:
membership in the approved pool is the verified signal, so similarity over the
pool degrades to pure relevance ranking.
"""

from collections.abc import Sequence
from datetime import datetime
from enum import Enum as StdEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.embeddings import EMBEDDING_DIM
from app.enums import ApprovalStatus, MealType


def _enum_values(enum_cls: type[StdEnum]) -> list[str]:
    """Persist enum values (e.g. 'breakfast'), not member names like 'BREAKFAST'."""
    return [member.value for member in enum_cls.__members__.values()]


def meal_embedding_text(name: str, description: str, tags: Sequence[str]) -> str:
    """The text a meal is embedded from: name, description, and tags joined.

    Stored and query vectors must come from the same model and the same source
    text, so this pins what the stored side embeds. The writer (composer) and any
    future re-embed call this rather than reconstructing the string, which keeps a
    dish-name query comparable to the stored vector.
    """
    return " ".join([name, description, *tags]).strip()


class CuratedMeal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One composed, code-verified, admin-approved histamine-safe meal."""

    __tablename__ = "curated_meals"

    name: Mapped[str]
    meal_type: Mapped[MealType] = mapped_column(
        Enum(
            MealType,
            native_enum=False,
            length=16,
            name="meal_type",
            create_constraint=True,
            values_callable=_enum_values,
        )
    )
    description: Mapped[str] = mapped_column(Text)
    ingredients: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    recipe: Mapped[list[str] | None] = mapped_column(JSONB, default=None)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'")
    )
    # Ingredients the index had no entry for: accepted by the automated gate (a
    # miss is unknown, not unsafe) but recorded so the approving admin sees exactly
    # what code could not vouch for.
    unverified_ingredients: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'")
    )
    # The producing model, surfaced on the transparency badge.
    model: Mapped[str]
    # Token usage of the composition (an LLMUsage blob), for the cost the badge
    # shows. Null for rows composed before this was recorded.
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    # The composer's authored act->observe->decide events, replayed as the board's
    # "watch the agent think" showcase.
    reasoning_trace: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'")
    )
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus,
            native_enum=False,
            length=16,
            name="approval_status",
            create_constraint=True,
            values_callable=_enum_values,
        ),
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
        # Every read filters on approved; index it so the filter does not seq-scan
        # the pool as it grows.
        index=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    approved_by: Mapped[str | None] = mapped_column(default=None)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    def __repr__(self) -> str:
        return f"<CuratedMeal {self.name!r} ({self.meal_type}): {self.approval_status}>"
