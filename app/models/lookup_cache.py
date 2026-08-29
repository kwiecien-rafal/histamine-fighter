"""ORM models for the dish-lookup caches.

Two tiers, both keyed by the canonical dish key (``lookup_source_key``). Writes
are gated to operator-trusted models at the route (shared tier on public
deployments); the human confirm step reviews a cached proposal anyway, and a
cached assessment is only served while its grounding fingerprint still matches
the live index. The stored ``model`` keeps the transparency badge truthful on a
hit. TTLs are garbage collection, not correctness.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_values
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.enums import SafetyLevel


class LookupProposalCache(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One cached ingredient proposal for a canonical dish key."""

    __tablename__ = "lookup_proposal_cache"

    dish_key: Mapped[str] = mapped_column(unique=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model: Mapped[str]
    # Indexed: the store-time GC delete filters on it.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    def __repr__(self) -> str:
        return f"<LookupProposalCache {self.dish_key!r} ({self.model})>"


class LookupAssessmentCache(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One cached assessment for a dish key and confirmed-ingredient set."""

    __tablename__ = "lookup_assessment_cache"

    dish_key: Mapped[str]
    ingredients_hash: Mapped[str]
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model: Mapped[str]
    # Fingerprint of everything the assessment was grounded in (index readings
    # plus substitute options); the serving gate — see compute_grounding.
    grounding_hash: Mapped[str]
    # The verdict the response was cached at, denormalized purely for
    # observability; the fingerprint, not the verdict, decides serving.
    verdict: Mapped[SafetyLevel] = mapped_column(
        Enum(
            SafetyLevel,
            native_enum=False,
            length=16,
            name="safety_level",
            create_constraint=True,
            values_callable=enum_values,
        )
    )
    # Indexed: the store-time GC delete filters on it.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        UniqueConstraint(
            "dish_key", "ingredients_hash", name="uq_lookup_assessment_cache_dish_ingredients"
        ),
    )

    def __repr__(self) -> str:
        return f"<LookupAssessmentCache {self.dish_key!r} ({self.verdict})>"
