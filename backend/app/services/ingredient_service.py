"""Read-side retrieval over the curated histamine ingredient index.

``find_candidates`` is the shared retrieval primitive: given a free-text
ingredient name it returns the relevant rows from the index, ranked, each
tagged with how it matched. The public endpoint shows these directly; the
dish-lookup agent (later) reasons over them with full dish context and applies
the cautious final verdict.

Retrieval favors recall and stays deterministic. Disambiguation and caution
live in the consumer, not here: a context-free matcher must not silently pick
one row when a name is genuinely ambiguous (egg yolk vs egg white).
"""

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.normalization import normalize_ingredient_name
from app.enums import Compatibility, MatchType
from app.models import HistamineIngredient

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IngredientMatch:
    """A retrieved ingredient together with how, and how strongly, it matched."""

    ingredient: HistamineIngredient
    match_type: MatchType
    score: float


def is_ambiguous(matches: list[IngredientMatch]) -> bool:
    """True when candidates disagree on compatibility (unrated counts as a value).

    Lives with the candidates so the endpoint and the dish agent share one
    definition of "ambiguous" rather than each computing their own.
    """
    return len({match.ingredient.compatibility for match in matches}) > 1


class IngredientService:
    """Retrieves ingredients from the curated index.

    Takes a request-scoped session and never commits; the transaction boundary
    stays with the caller (the FastAPI dependency).
    """

    # Minimum trigram similarity for a fuzzy match. From the seeded data, real
    # variants score ~0.5+ while unrelated words stay below ~0.25.
    fuzzy_floor = 0.3

    # Within the fuzzy matches, keep those scoring at least this fraction of the
    # best fuzzy score and drop the long tail of weak, irrelevant hits.
    relevance_ratio = 0.75

    # Most rows the index returns for one query; the consumer only needs a few.
    candidate_limit = 5

    # Ingredient names are short. The agent calls this directly, bypassing the
    # route's length cap, so bound the input here too: longer is never a real
    # ingredient, and we must not run a trigram scan on an oversized string.
    max_query_length = 200

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_candidates(self, name: str) -> list[IngredientMatch]:
        """Return the index's candidate matches for a name, best first.

        An empty list means the ingredient is not in the index. Callers must
        treat that as "unknown" and never infer that it is safe.
        """
        query = normalize_ingredient_name(name)
        if not query or len(query) > self.max_query_length:
            log.debug("ingredient.lookup.rejected_input", chars=len(query), preview=name[:60])
            return []

        exact = await self._match_exact(query)
        if exact is not None:
            # An exact canonical-name hit is treated as the ingredient itself, so
            # we stop here. This assumes curated names are specific, not ambiguous
            # umbrella terms: model "egg" as Whole Egg plus an "egg" alias, never
            # as a bare "Egg" row, or this would suppress the yolk/white variants
            # the ambiguity handling exists to surface.
            result = [exact]
        else:
            matches = await self._match_aliases(query) + await self._match_fuzzy(query)
            result = self._rank_unique(matches)[: self.candidate_limit]

        log.debug(
            "ingredient.candidates",
            query=query,
            count=len(result),
            names=[match.ingredient.name for match in result],
        )
        return result

    async def find_substitutes(self, category: str, limit: int = 3) -> list[HistamineIngredient]:
        """Return well-tolerated ingredients in a category, as grounded safe swaps.

        Used to keep the dish agent's swap suggestions honest: a proposed
        replacement should come from the index's known-good rows, not the model's
        imagination. "Safe" here means explicitly ``well_tolerated`` — an unrated
        row is not evidence of safety, only the absence of a recorded concern.
        """
        stmt = (
            select(HistamineIngredient)
            .where(
                HistamineIngredient.category == category,
                HistamineIngredient.compatibility == Compatibility.WELL_TOLERATED,
            )
            .order_by(HistamineIngredient.name)
            .limit(limit)
        )
        return list((await self._session.scalars(stmt)).all())

    async def _match_exact(self, query: str) -> IngredientMatch | None:
        stmt = select(HistamineIngredient).where(HistamineIngredient.normalized_name == query)
        row = (await self._session.scalars(stmt)).first()
        return IngredientMatch(row, MatchType.EXACT, 1.0) if row is not None else None

    async def _match_aliases(self, query: str) -> list[IngredientMatch]:
        # Aliases are normalized at write time, so the query (already normalized)
        # compares by plain array membership. A sequential scan is fine at this size.
        stmt = select(HistamineIngredient).where(
            HistamineIngredient.normalized_aliases.contains([query])
        )
        rows = (await self._session.scalars(stmt)).all()
        return [IngredientMatch(row, MatchType.ALIAS, 1.0) for row in rows]

    async def _match_fuzzy(self, query: str) -> list[IngredientMatch]:
        score = func.similarity(HistamineIngredient.normalized_name, query)
        stmt = (
            select(HistamineIngredient, score.label("score"))
            .where(score >= self.fuzzy_floor)
            # id breaks score ties collation-independently, so which rows survive
            # the limit is deterministic; the output order is set in Python below.
            .order_by(score.desc(), HistamineIngredient.id)
            .limit(self.candidate_limit * 2)
        )
        rows = [(ing, float(sim)) for ing, sim in (await self._session.execute(stmt)).all()]
        if not rows:
            return []
        # Keep the relevant cluster, drop the weak tail (relative to the best hit).
        cutoff = rows[0][1] * self.relevance_ratio
        return [IngredientMatch(ing, MatchType.FUZZY, sim) for ing, sim in rows if sim >= cutoff]

    @staticmethod
    def _rank_unique(matches: list[IngredientMatch]) -> list[IngredientMatch]:
        """Drop duplicate rows (keeping the strongest match), ordered for output."""
        strongest: dict[uuid.UUID, IngredientMatch] = {}
        for match in matches:
            current = strongest.get(match.ingredient.id)
            if current is None or match.score > current.score:
                strongest[match.ingredient.id] = match
        return sorted(strongest.values(), key=lambda m: (-m.score, m.ingredient.name))
