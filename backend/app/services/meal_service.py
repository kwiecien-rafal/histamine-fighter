"""Retrieval over the curated, admin-approved meal pool (the meal RAG read side).

``search`` embeds the query and returns the nearest *approved* meals by cosine
similarity, dropping matches below a floor so a thin pool returns nothing rather
than weak neighbours, so the caller (alternatives) then falls back to generation.
Membership in the approved pool is what makes similarity safe here: every row is
verified, so similarity degrades to pure relevance ranking. An exact distance scan
is used (no ANN index) because the pool is small, so it is both faster and exact.
The embedder is injected so a test can pass a deterministic stand-in without
loading the model.
"""

from collections.abc import Collection
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.normalization import normalize_ingredient_name
from app.embeddings import Embedder
from app.enums import ApprovalStatus, MealType
from app.models import CuratedMeal

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MealMatch:
    """A retrieved meal and its cosine similarity to the query (1.0 = identical)."""

    meal: CuratedMeal
    similarity: float


@dataclass(frozen=True, slots=True)
class _ExcludeTerms:
    """Exclude terms prepared once per query, then matched per meal ingredient.

    A category matches exactly (it is a controlled vocabulary). An ingredient
    *name* matches when its tokens and a term's tokens are subset-related either
    way, so avoiding "tomato sauce" still drops a meal listing "tomato", and
    avoiding "wine" drops "red wine" without "egg" dropping "eggplant" (distinct
    single tokens). This is lexical, not semantic; resolving both sides through
    the ingredient index is a deliberate future upgrade.
    """

    exact: frozenset[str]
    token_sets: tuple[frozenset[str], ...]

    @classmethod
    def from_terms(cls, terms: Collection[str]) -> "_ExcludeTerms":
        keys = [key for term in terms if (key := normalize_ingredient_name(term))]
        return cls(frozenset(keys), tuple(frozenset(key.split()) for key in keys))

    def matches(self, name: str, category: str) -> bool:
        if category and category in self.exact:
            return True
        if not name:
            return False
        name_tokens = frozenset(name.split())
        return any(name_tokens <= term or term <= name_tokens for term in self.token_sets)


class MealService:
    """Reads the approved meal pool by vector similarity. Never commits."""

    default_k = 5
    # The pool is verified-safe by construction, so similarity here is pure
    # relevance, not a safety signal. The floor keeps a weak long-tail neighbour
    # from surfacing as a confident "from our kitchen" pick. The caller falls back
    # to generation instead. Tied to the embedding model, so re-tune via the meal
    # retrieval eval if the model changes, exactly as the knowledge floor is.
    default_min_similarity = 0.75
    # Queries are dish names or short flavour-term lists, so this is generous and
    # deliberately its own value, not the knowledge Q&A cap: anything longer is a
    # caller bug, not a real query, and must not run as an oversized embed.
    max_query_length = 512

    def __init__(
        self,
        session: AsyncSession,
        embedder: Embedder,
        *,
        min_similarity: float | None = None,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._min_similarity = (
            self.default_min_similarity if min_similarity is None else min_similarity
        )

    async def search(
        self,
        query: str,
        *,
        meal_type: MealType | None = None,
        k: int | None = None,
        exclude: Collection[str] = (),
    ) -> list[MealMatch]:
        """Return the k most similar approved meals above the floor, best first.

        Only ``approved`` meals are eligible. ``meal_type`` narrows to one slot,
        and ``exclude`` drops any meal that uses a listed term (matched on an
        ingredient's category exactly, or on its name by word-set containment, so
        avoiding "tomato sauce" still drops a meal listing "tomato"), so a dish
        built on what the user is avoiding is never offered back. An empty list
        means nothing relevant was found (or the query was empty). An over-long
        query or a non-positive k raises ValueError instead, so a caller's bug
        never masquerades as "no match".
        """
        if k is not None and k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        text = query.strip()
        if len(text) > self.max_query_length:
            raise ValueError(f"query exceeds {self.max_query_length} characters: got {len(text)}")
        if not text:
            return []
        limit = self.default_k if k is None else k
        exclusions = _ExcludeTerms.from_terms(exclude)

        vector = await self._embedder.embed_query(text)
        distance = CuratedMeal.embedding.cosine_distance(vector)
        stmt = (
            select(CuratedMeal, distance.label("distance"))
            .where(CuratedMeal.approval_status == ApprovalStatus.APPROVED)
            .order_by(distance)
            # The vector is only needed for the SQL distance, never in Python.
            .options(defer(CuratedMeal.embedding))
        )
        if meal_type is not None:
            stmt = stmt.where(CuratedMeal.meal_type == meal_type)

        # Filter and cap in Python, not via SQL LIMIT: an excluded meal must not
        # consume a slot, and the floor stops the ordered scan as soon as a row
        # falls below it. The pool is small, so reading the ordered rows is as
        # cheap as the exact, ANN-free scan it already is.
        matches: list[MealMatch] = []
        above_floor = 0
        for meal, dist in (await self._session.execute(stmt)).all():
            similarity = 1.0 - float(dist)
            if similarity < self._min_similarity:
                break
            above_floor += 1
            if self._is_excluded(meal, exclusions):
                continue
            matches.append(MealMatch(meal, similarity))
            if len(matches) == limit:
                break

        log.debug(
            "meal.search",
            query=text[:80],
            meal_type=meal_type,
            kept=len(matches),
            above_floor=above_floor,
        )
        return matches

    async def random_sample(
        self,
        *,
        meal_type: MealType | None = None,
        k: int | None = None,
        exclude: Collection[str] = (),
    ) -> list[CuratedMeal]:
        """Return up to k random approved meals, optionally restricted to one slot.

        For the "any meal" alternatives goal (and future variety): there is no query
        to rank by, so it samples at random. ``exclude`` drops any meal that uses a
        listed term, matched as in ``search``, so a dish built on what the user is
        avoiding is never offered back. A non-positive k raises rather than silently
        falling back to the default.
        """
        if k is not None and k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        limit = self.default_k if k is None else k
        exclusions = _ExcludeTerms.from_terms(exclude)

        stmt = (
            select(CuratedMeal)
            .where(CuratedMeal.approval_status == ApprovalStatus.APPROVED)
            .options(defer(CuratedMeal.embedding))
        )
        if meal_type is not None:
            stmt = stmt.where(CuratedMeal.meal_type == meal_type)
        stmt = stmt.order_by(func.random())

        meals: list[CuratedMeal] = []
        for meal in (await self._session.execute(stmt)).scalars():
            if self._is_excluded(meal, exclusions):
                continue
            meals.append(meal)
            if len(meals) == limit:
                break
        return meals

    @staticmethod
    def _is_excluded(meal: CuratedMeal, terms: _ExcludeTerms) -> bool:
        """True when any of the meal's ingredients matches an excluded term."""
        if not terms.exact:
            return False
        return any(
            terms.matches(
                normalize_ingredient_name(ingredient.get("name", "")),
                normalize_ingredient_name(ingredient.get("category") or ""),
            )
            for ingredient in meal.ingredients
        )
