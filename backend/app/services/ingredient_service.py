"""Read-side retrieval over the curated histamine ingredient index.

``find_candidates`` is the shared retrieval primitive: given a free-text
ingredient name it returns the relevant rows from the index, ranked, each
tagged with how it matched. The public endpoint shows these directly; the
dish-lookup agent (later) reasons over them with full dish context and applies
the cautious final verdict.

Retrieval is tiered — exact name, then aliases, then fuzzy — and the strongest
curated tier that hits wins outright, so a trigram neighbour can never dilute a
name the curator spelled out. Within a tier it favors recall and stays
deterministic. Disambiguation and caution live in the consumer, not here: a
context-free matcher must not silently pick one row when a name is genuinely
ambiguous (egg yolk vs egg white).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from sqlalchemy import func, or_, select
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
            # the ambiguity handling exists to surface. Umbrella rows marked
            # is_category are the exception; find_category_candidates serves those.
            result = [exact]
        else:
            # Same tiering one level down: an alias hit is the curator saying
            # "this name is this row", so trigram neighbours are noise next to it
            # ("ground beef" must not let a fuzzy "Beef" dilute the Minced Meat
            # alias). Fuzzy is the fallback tier only. The exact-hit contract
            # above extends here: a name shared by several variants must be an
            # alias on every variant, or one alias would suppress the others.
            matches = await self._match_aliases(query)
            if not matches:
                matches = await self._match_fuzzy(query)
            result = self._rank_unique(matches)[: self.candidate_limit]

        log.debug(
            "ingredient.candidates",
            query=query,
            count=len(result),
            names=[match.ingredient.name for match in result],
        )
        return result

    async def find_candidates_many(self, names: Sequence[str]) -> dict[str, list[IngredientMatch]]:
        """Resolve a whole confirmed list at once, keyed by input name.

        Same per-name tiering as :meth:`find_candidates` (an exact name wins
        outright, an alias then suppresses fuzzy), but the common primary path
        collapses to one exact query and one alias query for the entire set
        rather than two round-trips per name. Only the residual misses fall back
        to the per-name fuzzy scan, which stays serial because the rows that
        reach it are rare. An empty or overlong name maps to an empty list.
        """
        results: dict[str, list[IngredientMatch]] = {}
        query_by_name: dict[str, str] = {}
        for name in names:
            query = normalize_ingredient_name(name)
            if not query or len(query) > self.max_query_length:
                results[name] = []
            else:
                query_by_name[name] = query

        queries = set(query_by_name.values())
        if not queries:
            return results

        exact = await self._match_exact_many(queries)
        misses = queries - exact.keys()
        aliases = await self._match_aliases_many(misses) if misses else {}

        resolved: dict[str, list[IngredientMatch]] = {}
        for query in queries:
            if query in exact:
                resolved[query] = [exact[query]]
                continue
            alias_matches = aliases.get(query)
            matches = alias_matches if alias_matches else await self._match_fuzzy(query)
            resolved[query] = self._rank_unique(matches)[: self.candidate_limit]

        for name, query in query_by_name.items():
            results[name] = resolved[query]
        return results

    async def find_category_candidates(self, category: str) -> list[IngredientMatch]:
        """Resolve a category descriptor against umbrella rows, by exact match only.

        The dish agent's fallback when a specific ingredient misses the index: a
        descriptor like "aged hard cheese" resolves to the curated umbrella row
        covering that group. Only rows marked ``is_category`` are eligible, and
        matching is exact (name or alias) — deliberately no fuzzy, so a free-text
        descriptor like "meat" cannot drift into specific cured-meat rows. An
        empty list means the index knows no such category.
        """
        query = normalize_ingredient_name(category)
        if not query or len(query) > self.max_query_length:
            log.debug("ingredient.category.rejected_input", chars=len(query), preview=category[:60])
            return []

        stmt = select(HistamineIngredient).where(
            HistamineIngredient.is_category.is_(True),
            or_(
                HistamineIngredient.normalized_name == query,
                HistamineIngredient.normalized_aliases.contains([query]),
            ),
        )
        rows = (await self._session.scalars(stmt)).all()
        matches = [
            IngredientMatch(
                row,
                MatchType.EXACT if row.normalized_name == query else MatchType.ALIAS,
                1.0,
            )
            for row in rows
        ]
        result = self._rank_unique(matches)[: self.candidate_limit]

        log.debug(
            "ingredient.category_candidates",
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

    # The rated levels the dish verdict treats as risky; an unrated row is not
    # evidence of safety but it does not flag a recipe either, so it is excluded.
    _RISKY_COMPATIBILITIES = (
        Compatibility.MODERATELY_COMPATIBLE,
        Compatibility.INCOMPATIBLE,
        Compatibility.POORLY_TOLERATED,
    )

    async def risky_terms(self) -> list[str]:
        """Normalized names and aliases of every index row rated worse than safe.

        The composer scans a submitted recipe against these so a high-histamine
        ingredient written into the steps (never added to the verified list) is
        caught, not just one listed in the ingredients.
        """
        stmt = select(
            HistamineIngredient.normalized_name, HistamineIngredient.normalized_aliases
        ).where(HistamineIngredient.compatibility.in_(self._RISKY_COMPATIBILITIES))
        terms: list[str] = []
        for name, aliases in (await self._session.execute(stmt)).all():
            terms.append(name)
            terms.extend(aliases)
        return terms

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

    async def _match_exact_many(self, queries: set[str]) -> dict[str, IngredientMatch]:
        # normalized_name is unique, so each query maps to at most one row.
        stmt = select(HistamineIngredient).where(HistamineIngredient.normalized_name.in_(queries))
        rows = (await self._session.scalars(stmt)).all()
        return {row.normalized_name: IngredientMatch(row, MatchType.EXACT, 1.0) for row in rows}

    async def _match_aliases_many(self, queries: set[str]) -> dict[str, list[IngredientMatch]]:
        # One overlap (PG &&) query for the whole miss set; a row can carry an
        # alias for several queries at once, so it is assigned to each it matches.
        stmt = select(HistamineIngredient).where(
            HistamineIngredient.normalized_aliases.overlap(list(queries))
        )
        rows = (await self._session.scalars(stmt)).all()
        by_query: dict[str, list[IngredientMatch]] = {}
        for row in rows:
            match = IngredientMatch(row, MatchType.ALIAS, 1.0)
            for alias in row.normalized_aliases:
                if alias in queries:
                    by_query.setdefault(alias, []).append(match)
        return by_query

    @staticmethod
    def _fuzzy_floor(query: str) -> float:
        """Minimum trigram similarity to accept, stricter for short queries.

        A short name shares its handful of trigrams with unrelated words, so a
        flat floor lets four-letter queries collide ("salt" scores 0.33 against
        "salami"). The 0.4 short-query floor was chosen to clear the observed
        collisions and is guarded by the retrieval eval
        (tests/test_retrieval_eval.py): above the salt/salami collision (0.33),
        below the genuine egg/egg-white match (0.44). Longer names keep the
        looser 0.3 so real typos still resolve ("chedar" finds Cheddar).
        """
        return 0.4 if len(query) <= 6 else 0.3

    async def _match_fuzzy(self, query: str) -> list[IngredientMatch]:
        score = func.similarity(HistamineIngredient.normalized_name, query)
        stmt = (
            select(HistamineIngredient, score.label("score"))
            .where(score >= self._fuzzy_floor(query))
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
