"""TTL caches for the dish lookup (the cost-control side of the flagship flow).

Three tiers, all keyed by the canonical dish key. The invariant the assessment
and rewrite tiers hold: cached content is always the output of an operator-trusted model
(the routes only store shared-tier responses on public deployments), and it is
served only when the curated index it was grounded in is provably unchanged.
That proof is the grounding fingerprint — a hash over every index reading and
substitute option the assessment could have drawn on, recomputed from the live
index on each hit-candidate (pure DB reads, no model call) and compared to the
hash stored at write time. Any index drift, however small, reads as a miss and
a fresh assessment overwrites the row. That is what makes the long TTL safe:
staleness cannot outlive the data it was grounded in. Never commits; the
request session owns the transaction.

The rewrite tier fingerprints both sides — the readings its decisions came from
and the readings that cleared the list it produced — because what it hands back
is a dish to cook, not a verdict on one already on the plate.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import RewriteOutcome, SafetyLevel
from app.models import LookupAssessmentCache, LookupProposalCache, LookupRewriteCache
from app.schemas.meal import (
    AdaptedDish,
    ConfirmedIngredient,
    DishAssessmentResponse,
    IngredientProposalResponse,
    lookup_source_key,
)
from app.services.ingredient_lookup import (
    SUBSTITUTE_LIMIT,
    candidates_safety,
    lookup_ingredients,
    worst_risky,
)
from app.services.ingredient_service import IngredientService

log = structlog.get_logger(__name__)

CachedResponseT = TypeVar("CachedResponseT", bound=BaseModel)


def _parsed(
    model: type[CachedResponseT], payload: dict[str, Any], *, tier: str
) -> CachedResponseT | None:
    """Parse a stored row, reading one the current schema cannot load as a miss."""
    try:
        return model.model_validate(payload)
    except ValidationError:
        log.warning("lookup_cache.unreadable_row", tier=tier)
        return None


# The response fields that never come from the cache row: usage is zero on a
# hit, cached is set by the serving side, and dish echoes the caller's text.
_SERVE_OVERRIDES = {"usage", "cached"}


def _stable_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def ingredients_hash(ingredients: Sequence[ConfirmedIngredient]) -> str:
    """One stable hash for a confirmed ingredient set, order-insensitive."""
    pairs = sorted([item.name.casefold(), (item.category or "").casefold()] for item in ingredients)
    return _stable_hash(pairs)


async def compute_grounding(
    service: IngredientService, ingredients: Sequence[ConfirmedIngredient]
) -> str | None:
    """Fingerprint everything an assessment of these ingredients is grounded in."""
    lookups = await lookup_ingredients(
        service, [(item.name, item.category) for item in ingredients]
    )
    if any(lookup.error for lookup in lookups):
        return None
    readings: list[dict[str, object]] = []
    substitute_categories: set[str] = set()
    for lookup in sorted(lookups, key=lambda entry: entry.ingredient):
        candidates = sorted(lookup.candidates, key=lambda c: (c.name, c.compatibility))
        readings.append(
            {
                "ingredient": lookup.ingredient,
                "found": lookup.found,
                "ambiguous": lookup.ambiguous,
                "matched_on": lookup.matched_on,
                "candidates": [
                    {
                        "name": c.name,
                        "compatibility": c.compatibility,
                        "mechanisms": sorted(m.value for m in c.mechanisms),
                        "category": c.category,
                        "notes": c.notes,
                    }
                    for c in candidates
                ],
            }
        )
        if candidates_safety(lookup.candidates) is SafetyLevel.AVOID:
            worst = worst_risky(lookup.candidates)
            if worst is not None and worst.category:
                substitute_categories.add(worst.category)
    substitutes: dict[str, list[str]] = {}
    for category in sorted(substitute_categories):
        rows = await service.find_substitutes(category, limit=SUBSTITUTE_LIMIT)
        substitutes[category] = sorted(row.name for row in rows)
    return _stable_hash({"readings": readings, "substitutes": substitutes})


class LookupCacheService:
    """Reads and writes the dish-lookup proposal, assessment and rewrite caches."""

    def __init__(
        self,
        session: AsyncSession,
        ingredient_service: IngredientService,
        *,
        ttl_days: int | None = None,
    ) -> None:
        self._session = session
        self._ingredients = ingredient_service
        self._ttl = timedelta(days=settings.lookup_cache_ttl_days if ttl_days is None else ttl_days)

    async def get_proposal(self, dish: str) -> IngredientProposalResponse | None:
        """The cached proposal for this dish, or None; echoes the caller's text."""
        key = lookup_source_key(dish)
        if not key:
            return None
        stmt = select(LookupProposalCache.response).where(
            LookupProposalCache.dish_key == key,
            LookupProposalCache.expires_at > datetime.now(UTC),
        )
        payload = (await self._session.execute(stmt)).scalar_one_or_none()
        if payload is None:
            return None
        proposal = _parsed(
            IngredientProposalResponse,
            {**payload, "dish": dish, "cached": True, "usage": {}},
            tier="proposal",
        )
        if proposal is None:
            return None
        log.info("lookup_cache.proposal_hit", model=payload.get("model"))
        return proposal

    async def store_proposal(self, response: IngredientProposalResponse) -> None:
        """Upsert a recognized, non-empty proposal; junk is never worth freezing."""
        if not response.recognized or not response.ingredients:
            return
        key = lookup_source_key(response.dish)
        if not key:
            return
        now = datetime.now(UTC)
        await self._session.execute(
            delete(LookupProposalCache).where(LookupProposalCache.expires_at <= now)
        )
        stmt = insert(LookupProposalCache).values(
            dish_key=key,
            response=response.model_dump(exclude=_SERVE_OVERRIDES),
            model=response.model,
            expires_at=now + self._ttl,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_lookup_proposal_cache_dish_key",
            set_={
                "response": stmt.excluded.response,
                "model": stmt.excluded.model,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        await self._session.execute(stmt)

    async def get_assessment(
        self, dish: str, ingredients: Sequence[ConfirmedIngredient]
    ) -> DishAssessmentResponse | None:
        """The cached assessment, served only if its grounding is provably unchanged."""
        key = lookup_source_key(dish)
        if not key:
            return None
        stmt = select(LookupAssessmentCache).where(
            LookupAssessmentCache.dish_key == key,
            LookupAssessmentCache.ingredients_hash == ingredients_hash(ingredients),
            LookupAssessmentCache.expires_at > datetime.now(UTC),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None

        grounding = await compute_grounding(self._ingredients, ingredients)
        if grounding is None:
            log.warning("lookup_cache.regrade_incomplete", dish_key=key)
            return None
        if grounding != row.grounding_hash:
            # The index moved under this row; a fresh assess will overwrite it.
            log.info("lookup_cache.grounding_moved", cached_verdict=row.verdict.value)
            return None
        assessment = _parsed(
            DishAssessmentResponse, {**row.response, "cached": True, "usage": {}}, tier="assessment"
        )
        if assessment is None:
            return None
        log.info("lookup_cache.assessment_hit", verdict=row.verdict.value, model=row.model)
        return assessment

    async def store_assessment(
        self,
        dish: str,
        ingredients: Sequence[ConfirmedIngredient],
        response: DishAssessmentResponse,
    ) -> None:
        """Upsert an assessment whose grounding was complete and is fingerprinted."""
        if any(item.error for item in response.ingredients):
            return
        key = lookup_source_key(dish)
        if not key:
            return
        grounding = await compute_grounding(self._ingredients, ingredients)
        if grounding is None:
            return
        now = datetime.now(UTC)
        await self._session.execute(
            delete(LookupAssessmentCache).where(LookupAssessmentCache.expires_at <= now)
        )
        stmt = insert(LookupAssessmentCache).values(
            dish_key=key,
            ingredients_hash=ingredients_hash(ingredients),
            response=response.model_dump(exclude=_SERVE_OVERRIDES),
            model=response.model,
            verdict=response.verdict,
            grounding_hash=grounding,
            expires_at=now + self._ttl,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_lookup_assessment_cache_dish_ingredients",
            set_={
                "response": stmt.excluded.response,
                "model": stmt.excluded.model,
                "verdict": stmt.excluded.verdict,
                "grounding_hash": stmt.excluded.grounding_hash,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        await self._session.execute(stmt)

    async def get_rewrite(
        self, dish: str, ingredients: Sequence[ConfirmedIngredient]
    ) -> AdaptedDish | None:
        """The cached rewrite for this dish and input list, if still grounded."""
        key = lookup_source_key(dish)
        if not key:
            return None
        stmt = select(LookupRewriteCache).where(
            LookupRewriteCache.dish_key == key,
            LookupRewriteCache.ingredients_hash == ingredients_hash(ingredients),
            LookupRewriteCache.expires_at > datetime.now(UTC),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None

        response = _parsed(
            AdaptedDish, {**row.response, "cached": True, "usage": {}}, tier="rewrite"
        )
        if response is None:
            return None
        grounding = await self._rewrite_grounding(ingredients, response)
        if grounding is None:
            log.warning("lookup_cache.rewrite_regrade_incomplete", dish_key=key)
            return None
        if grounding != row.grounding_hash:
            # The index moved under this row; a fresh rewrite will overwrite it.
            log.info("lookup_cache.rewrite_grounding_moved", cached_verdict=row.verdict.value)
            return None
        log.info("lookup_cache.rewrite_hit", verdict=row.verdict.value, model=row.model)
        return response

    async def store_rewrite(
        self, dish: str, ingredients: Sequence[ConfirmedIngredient], response: AdaptedDish
    ) -> None:
        """Upsert a rewrite that actually produced a dish, fingerprinting both sides."""
        if response.outcome is not RewriteOutcome.ADAPTED:
            return
        key = lookup_source_key(dish)
        if not key:
            return
        grounding = await self._rewrite_grounding(ingredients, response)
        if grounding is None:
            return
        now = datetime.now(UTC)
        await self._session.execute(
            delete(LookupRewriteCache).where(LookupRewriteCache.expires_at <= now)
        )
        stmt = insert(LookupRewriteCache).values(
            dish_key=key,
            ingredients_hash=ingredients_hash(ingredients),
            response=response.model_dump(exclude=_SERVE_OVERRIDES),
            model=response.model,
            verdict=response.verdict,
            grounding_hash=grounding,
            expires_at=now + self._ttl,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_lookup_rewrite_cache_dish_ingredients",
            set_={
                "response": stmt.excluded.response,
                "model": stmt.excluded.model,
                "verdict": stmt.excluded.verdict,
                "grounding_hash": stmt.excluded.grounding_hash,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        await self._session.execute(stmt)

    async def _rewrite_grounding(
        self, ingredients: Sequence[ConfirmedIngredient], response: AdaptedDish
    ) -> str | None:
        """Fingerprint both the list the rewrite was asked about and the one it returned."""
        asked = await compute_grounding(self._ingredients, ingredients)
        produced = await compute_grounding(
            self._ingredients,
            [
                ConfirmedIngredient(name=item.name, category=item.category)
                for item in response.ingredients
            ],
        )
        if asked is None or produced is None:
            return None
        return _stable_hash({"asked": asked, "produced": produced})
