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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


class MealService:
    """Reads the approved meal pool by vector similarity. Never commits."""

    default_k = 5
    # The pool is verified-safe by construction, so similarity here is pure
    # relevance, not a safety signal. The floor keeps a weak long-tail neighbour
    # from surfacing as a confident "from our kitchen" pick. The caller falls back
    # to generation instead. Tied to the embedding model, so re-tune via the meal
    # retrieval eval if the model changes, exactly as the knowledge floor is.
    default_min_similarity = 0.75
    # Queries are dish names or short flavour-term lists. Anything longer is a
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
        and ``exclude`` drops any meal whose ingredient names or categories include a
        listed term, so a dish built on what the user is avoiding is never offered
        back. An empty list means nothing relevant was found (or the query was
        empty). An over-long query or a non-positive k raises ValueError instead,
        so a caller's bug never masquerades as "no match".
        """
        if k is not None and k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        text = query.strip()
        if len(text) > self.max_query_length:
            raise ValueError(f"query exceeds {self.max_query_length} characters: got {len(text)}")
        if not text:
            return []
        limit = self.default_k if k is None else k
        excluded = {key for term in exclude if (key := normalize_ingredient_name(term))}

        vector = await self._embedder.embed_query(text)
        distance = CuratedMeal.embedding.cosine_distance(vector)
        stmt = (
            select(CuratedMeal, distance.label("distance"))
            .where(CuratedMeal.approval_status == ApprovalStatus.APPROVED)
            .order_by(distance)
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
            if self._is_excluded(meal, excluded):
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

    @staticmethod
    def _is_excluded(meal: CuratedMeal, excluded: set[str]) -> bool:
        """True when any of the meal's ingredient names or categories is excluded."""
        if not excluded:
            return False
        for ingredient in meal.ingredients:
            name = normalize_ingredient_name(ingredient.get("name", ""))
            category = normalize_ingredient_name(ingredient.get("category") or "")
            if name in excluded or category in excluded:
                return True
        return False
