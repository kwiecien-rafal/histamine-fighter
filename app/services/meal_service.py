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
from app.schemas.meal import (
    CautionedIngredient,
    ComposedMeal,
    ProposedIngredient,
    PublicMealCard,
    PublicMealDetail,
    TraceEvent,
    public_trace,
)
from app.services.ingredient_service import IngredientService
from app.services.meal_edit import (
    EditTargetMissing,
    EditTargetNotPending,
    ensure_safe,
    verify_edit,
)

log = structlog.get_logger(__name__)

# The ``model`` value a hand-authored meal carries in place of a producing model, so a
# manual meal tells itself apart from a composed one without an extra discriminator column
# (which would mean a migration this rework deliberately avoids). The templates read this
# constant to render it as "Curated by admin"; an empty trace alongside it means no replay.
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
        """Return the k most similar approved meals above the floor, best first."""
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
        """Return up to k random approved meals, optionally restricted to one slot."""
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

    async def count_approved(self) -> int:
        """How many meals are in the public pool, for callers that want the size only."""
        result = await self._session.execute(
            select(func.count())
            .select_from(CuratedMeal)
            .where(CuratedMeal.approval_status == ApprovalStatus.APPROVED)
        )
        return int(result.scalar_one())

    async def list_approved(
        self, *, meal_type: MealType | None = None, limit: int, offset: int = 0
    ) -> tuple[list[CuratedMeal], int]:
        """One page of approved meals for the public browse, plus the total that match."""
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
        """One approved meal by id for the public detail, or None when it is not public."""
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
        """Shape a composed meal into a pending curated row and add it to the session."""
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
            cautioned_ingredients=[item.model_dump() for item in meal.cautioned_ingredients],
            model=meal.model,
            usage=meal.usage.model_dump(),
            reasoning_trace=[event.model_dump() for event in meal.reasoning_trace],
            approval_status=ApprovalStatus.PENDING,
            embedding=vector,
        )
        self._session.add(row)
        return row

    async def store_manual(
        self,
        fields: AdminMealCreate,
        *,
        unverified: list[str],
        cautioned: list[CautionedIngredient],
        actor: str,
    ) -> CuratedMeal:
        """Build a hand-written meal as a pending curated row, no composer involved."""
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
            cautioned_ingredients=[item.model_dump() for item in cautioned],
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

    async def create_manual(
        self,
        payload: AdminMealCreate,
        *,
        actor: str,
        ingredients: IngredientService,
    ) -> CuratedMeal:
        """Store a hand-written meal as pending, once the index gate lets it through."""
        verification = await verify_edit(ingredients, payload)
        confirmed_flags = ensure_safe(verification, confirmed=payload.confirm_flagged)
        row = await self.store_manual(
            payload,
            unverified=verification.unverified + confirmed_flags,
            cautioned=verification.cautioned,
            actor=actor,
        )
        await self._session.flush()
        return row

    async def edit_pending(
        self,
        meal_id: UUID,
        payload: AdminMealUpdate,
        *,
        ingredients: IngredientService,
    ) -> CuratedMeal:
        """Rewrite a pending curated meal, re-verified against the index before saving."""
        meal = await self.get(meal_id)
        if meal is None:
            raise EditTargetMissing("Meal not found.")
        if meal.approval_status is not ApprovalStatus.PENDING:
            raise EditTargetNotPending("Only a pending meal can be edited.")
        verification = await verify_edit(ingredients, payload)
        confirmed_flags = ensure_safe(verification, confirmed=payload.confirm_flagged)
        await self.apply_edit(
            meal,
            payload,
            unverified=verification.unverified + confirmed_flags,
            cautioned=verification.cautioned,
        )
        return meal

    async def apply_edit(
        self,
        meal: CuratedMeal,
        payload: AdminMealUpdate,
        *,
        unverified: list[str],
        cautioned: list[CautionedIngredient],
    ) -> None:
        """Apply a verified edit to a curated row, re-embedding only when text changed."""
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
        meal.cautioned_ingredients = [item.model_dump() for item in cautioned]
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


def public_card(row: CuratedMeal) -> PublicMealCard:
    """Shape an approved row into its lean browse-list card (no recipe or trace shipped)."""
    return PublicMealCard(
        id=row.id,
        meal_type=row.meal_type,
        model=row.model,
        name=row.name,
        description=row.description,
        tags=list(row.tags),
        has_recipe=bool(row.recipe),
        has_trace=bool(_public_events(row)),
    )


def public_detail(row: CuratedMeal) -> PublicMealDetail:
    """Shape an approved row into its full public detail, trace filtered."""
    return PublicMealDetail(
        id=row.id,
        meal_type=row.meal_type,
        model=row.model,
        name=row.name,
        description=row.description,
        ingredients=[ProposedIngredient.model_validate(item) for item in row.ingredients],
        recipe=row.recipe,
        tags=list(row.tags),
        cautioned_ingredients=[
            CautionedIngredient.model_validate(item) for item in row.cautioned_ingredients
        ],
        trace=_public_events(row),
    )


def _public_events(row: CuratedMeal) -> list[TraceEvent]:
    """The row's reasoning trace, validated and filtered to the steps a visitor may see."""
    return public_trace([TraceEvent.model_validate(event) for event in row.reasoning_trace])
