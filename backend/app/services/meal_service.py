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
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.normalization import normalize_ingredient_name
from app.core.term_match import TermMatcher
from app.embeddings import Embedder
from app.enums import ApprovalStatus, MealType
from app.models import CuratedMeal
from app.models.curated_meal import meal_embedding_text
from app.schemas.admin import AdminMealCreate, AdminMealUpdate
from app.schemas.meal import ComposedMeal

log = structlog.get_logger(__name__)

# The ``model`` value a hand-authored meal carries in place of a producing model, so a
# manual meal tells itself apart from a composed one without an extra discriminator column
# (which would mean a migration this rework deliberately avoids). The UI renders it as
# "Curated by admin"; an empty trace alongside it means no replay. Must stay in sync with
# MANUAL_MODEL in the frontend (api/domain.ts), which hardcodes the same literal.
MANUAL_MODEL = "manual"


@dataclass(frozen=True, slots=True)
class MealMatch:
    """A retrieved meal and its cosine similarity to the query (1.0 = identical)."""

    meal: CuratedMeal
    similarity: float


@dataclass(frozen=True, slots=True)
class _ExcludeTerms:
    """Exclude terms prepared once per query, then matched per meal ingredient.

    A category matches exactly (it is a controlled vocabulary). An ingredient
    *name* matches by token-set containment via the shared :class:`TermMatcher`,
    so avoiding "tomato sauce" still drops a meal listing "tomato", and avoiding
    "wine" drops "red wine" without "egg" dropping "eggplant" (distinct single
    tokens). Lexical, not semantic; resolving both sides through the ingredient
    index is a deliberate future upgrade.
    """

    exact: frozenset[str]
    names: TermMatcher

    @classmethod
    def from_terms(cls, terms: Collection[str]) -> "_ExcludeTerms":
        keys = [key for term in terms if (key := normalize_ingredient_name(term))]
        return cls(frozenset(keys), TermMatcher.from_terms(keys))

    def matches(self, name: str, category: str) -> bool:
        if category and category in self.exact:
            return True
        return bool(name) and self.names.matched(name)


class MealService:
    """Reads the approved meal pool by similarity, and stores composed pending meals.

    Never commits.
    """

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

    async def list_approved(
        self, *, meal_type: MealType | None = None, limit: int, offset: int = 0
    ) -> tuple[list[CuratedMeal], int]:
        """One page of approved meals for the public browse, plus the total that match.

        Ordered newest-composed first (``created_at``); the id breaks ties between rows
        a batch insert gave one timestamp. The total lets the browse page its way through
        the pool without guessing where it ends. The embedding column is heavy and unused
        by a browse card, so it is deferred. Read-only; never commits.
        """
        filters = [CuratedMeal.approval_status == ApprovalStatus.APPROVED]
        if meal_type is not None:
            filters.append(CuratedMeal.meal_type == meal_type)

        total = await self._session.scalar(
            select(func.count()).select_from(CuratedMeal).where(*filters)
        )
        stmt = (
            select(CuratedMeal)
            .where(*filters)
            .options(defer(CuratedMeal.embedding))
            .order_by(CuratedMeal.created_at.desc(), CuratedMeal.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        return rows, total or 0

    async def get_approved(self, meal_id: UUID) -> CuratedMeal | None:
        """One approved meal by id for the public detail, or None when it is not public.

        Folds the approved filter into the lookup so a pending or rejected row reads as
        absent, which the endpoint turns into a 404, never disclosing an unapproved meal.
        """
        stmt = (
            select(CuratedMeal)
            .where(
                CuratedMeal.id == meal_id,
                CuratedMeal.approval_status == ApprovalStatus.APPROVED,
            )
            .options(defer(CuratedMeal.embedding))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def store_pending(self, meal: ComposedMeal) -> CuratedMeal:
        """Shape a composed meal into a pending curated row and add it to the session.

        Embeds from the same name/description/tags text retrieval queries against, and
        stores the full reasoning trace, usage, and producing model. Marked pending so
        it is pool-eligible only once an admin approves. The caller (cron or the admin
        save) owns the commit.
        """
        vector = (
            await self._embedder.embed_documents(
                [meal_embedding_text(meal.name, meal.description, meal.tags)]
            )
        )[0]
        row = CuratedMeal(
            name=meal.name,
            meal_type=meal.meal_type,
            description=meal.description,
            ingredients=[ingredient.model_dump() for ingredient in meal.ingredients],
            recipe=meal.recipe,
            tags=meal.tags,
            unverified_ingredients=meal.unverified_ingredients,
            model=meal.model,
            usage=meal.usage.model_dump(),
            reasoning_trace=[event.model_dump() for event in meal.reasoning_trace],
            approval_status=ApprovalStatus.PENDING,
            embedding=vector,
        )
        self._session.add(row)
        return row

    async def store_manual(
        self, fields: AdminMealCreate, *, unverified: list[str], actor: str
    ) -> CuratedMeal:
        """Build a hand-written meal as a pending curated row, no composer involved.

        The manual counterpart to ``store_pending``: it embeds from the same name/
        description/tags text, so a manual meal ranks in retrieval exactly like a composed
        one, and lands pending for the same admin approval. Provenance is what differs: the
        ``manual`` sentinel model, no token usage, and an empty trace, so no replay offers.
        ``unverified`` is the index gate's not-indexed list, recorded for the approving
        admin. ``actor`` is the authoring admin, logged as the only record of who wrote a
        hand-authored meal (the row keeps no human author, where a composed one keeps its
        model). The caller commits.
        """
        vector = (
            await self._embedder.embed_documents(
                [meal_embedding_text(fields.name, fields.description, fields.tags)]
            )
        )[0]
        row = CuratedMeal(
            name=fields.name,
            meal_type=fields.meal_type,
            description=fields.description,
            ingredients=[ingredient.model_dump() for ingredient in fields.ingredients],
            recipe=fields.recipe,
            tags=fields.tags,
            unverified_ingredients=unverified,
            model=MANUAL_MODEL,
            usage=None,
            reasoning_trace=[],
            approval_status=ApprovalStatus.PENDING,
            embedding=vector,
        )
        self._session.add(row)
        log.info(
            "meal.created_manual", actor=actor, name=fields.name, meal_type=fields.meal_type.value
        )
        return row

    async def get(self, meal_id: UUID) -> CuratedMeal | None:
        """Return one curated meal by id, or None when there is no match."""
        return await self._session.get(CuratedMeal, meal_id)

    async def apply_edit(
        self, meal: CuratedMeal, payload: AdminMealUpdate, *, unverified: list[str]
    ) -> None:
        """Apply a verified edit to a curated row, re-embedding only when text changed.

        The embedding is recomputed only when the retrieval text (name, description, or
        tags) actually changed, so an ingredient or recipe edit does not pay for an embed.
        ``unverified`` is the re-derived not-indexed list. The caller commits.
        """
        reembed = (meal.name, meal.description, list(meal.tags)) != (
            payload.name,
            payload.description,
            payload.tags,
        )
        meal.name = payload.name
        meal.description = payload.description
        meal.ingredients = [item.model_dump() for item in payload.ingredients]
        meal.recipe = payload.recipe
        meal.tags = payload.tags
        meal.unverified_ingredients = unverified
        if reembed:
            meal.embedding = (
                await self._embedder.embed_documents(
                    [meal_embedding_text(meal.name, meal.description, meal.tags)]
                )
            )[0]

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
