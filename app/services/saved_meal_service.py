"""Service for per-user saved meals. Never commits; the request session owns that.

Saves are snapshots: curated and daily content is copied server-side from the
source row at save time (the route enforces the approval/reveal gates first), a
lookup save stores the client's normalized assessment. Inserts go through
``INSERT .. ON CONFLICT DO NOTHING`` so two racing taps on the same save button cannot
raise a unique violation into the request session; the loser reads the winner's
row. One consequence worth knowing: a daily slot the admin regenerates keeps its
id but goes back to pending and off the board, so a save pointing at it simply
goes stale until re-approval. Lookup saves are keyed on the client-minted
per-result id, so every assessment result can be saved on its own; a name that
collides with an existing save gets a " (n)" suffix instead of being rejected.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.recipe import RecipeAgent
from app.config import settings
from app.enums import ApprovalStatus, SavedMealTag, SaveSource
from app.llm.request import RequestLLM
from app.models import CuratedMeal, DailySuggestion, SavedMeal
from app.schemas.daily import DailyMealContent
from app.schemas.meal import MAX_DISH_CHARS, CautionedIngredient, ProposedIngredient
from app.schemas.saved import (
    SavedMealCard,
    SavedMealDetail,
    SavedMealUpdate,
    SavedRecipeResponse,
    SaveFromLookup,
    SaveRequest,
)
from app.schemas.usage import LLMUsage
from app.services.daily_service import DailyService
from app.services.meal_service import MealService


class SavedMealNotFound(Exception):
    """The save, or the source it would copy, is not there for this user.

    One error for both because they answer the same way on purpose: an unknown id,
    someone else's id, and a source that is not public yet must be indistinguishable,
    or a 404 becomes a probe. The API boundary maps this to 404.
    """


class SaveLimitReached(Exception):
    """The user is at the per-account save cap. The API boundary maps this to 409."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        super().__init__(f"Save limit reached ({cap}). Remove some first.")


class SavedMealService:
    """CRUD over one user's saved-meal snapshots. Never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for(self, user_id: UUID) -> list[SavedMeal]:
        """Every saved meal for the user, newest first. Capped, so no paging."""
        result = await self._session.execute(
            select(SavedMeal)
            .where(SavedMeal.user_id == user_id)
            .order_by(SavedMeal.created_at.desc())
        )
        return list(result.scalars())

    async def get(self, user_id: UUID, save_id: UUID) -> SavedMeal | None:
        """One saved meal, scoped to its owner so a foreign id reads as absent."""
        result = await self._session.execute(
            select(SavedMeal).where(SavedMeal.id == save_id, SavedMeal.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def set_recipe(
        self, user_id: UUID, save_id: UUID, steps: list[str], model: str
    ) -> SavedMeal | None:
        """Attach generated recipe steps and their producing model to the saved copy.

        First write wins: a row that already has a recipe is returned unchanged,
        so concurrent generations cannot overwrite each other. Not a user edit:
        ``edited_at`` stays untouched, so a verified badge is not lost to a
        recipe the app itself wrote.
        """
        row = await self.get(user_id, save_id)
        if row is None:
            return None
        if row.recipe:
            return row
        row.recipe = steps
        row.recipe_model = model
        return row

    async def find(self, user_id: UUID, source: SaveSource, source_key: str) -> SavedMeal | None:
        """The user's existing save for a source row, if any."""
        result = await self._session.execute(
            select(SavedMeal).where(
                SavedMeal.user_id == user_id,
                SavedMeal.source == source,
                SavedMeal.source_key == source_key,
            )
        )
        return result.scalar_one_or_none()

    async def saves_for_sources(
        self, user_id: UUID, source: SaveSource, source_keys: Sequence[str]
    ) -> dict[str, UUID]:
        """The user's save ids for these source rows, keyed by source key.

        Lets a page showing many meals mark what is already saved with one query
        instead of one per card.
        """
        if not source_keys:
            return {}
        result = await self._session.execute(
            select(SavedMeal.source_key, SavedMeal.id).where(
                SavedMeal.user_id == user_id,
                SavedMeal.source == source,
                SavedMeal.source_key.in_(source_keys),
            )
        )
        return {source_key: save_id for source_key, save_id in result}

    async def count_for(self, user_id: UUID) -> int:
        """How many saves the user holds, for the abuse cap."""
        result = await self._session.execute(
            select(func.count()).select_from(SavedMeal).where(SavedMeal.user_id == user_id)
        )
        return int(result.scalar_one())

    async def save_curated(self, user_id: UUID, row: CuratedMeal) -> tuple[SavedMeal, bool]:
        """Snapshot an approved curated meal. The route owns the approval gate."""
        return await self._upsert(
            user_id,
            SaveSource.CURATED,
            str(row.id),
            meal_type=row.meal_type,
            name=row.name,
            description=row.description,
            ingredients=list(row.ingredients),
            recipe=row.recipe,
            # Source tags are free-form; saved copies use the closed vocabulary,
            # so seeding starts from the meal slot alone.
            tags=[row.meal_type.value],
            cautioned_ingredients=list(row.cautioned_ingredients),
            model=row.model,
            verdict=None,
        )

    async def save_daily(self, user_id: UUID, row: DailySuggestion) -> tuple[SavedMeal, bool]:
        """Snapshot a revealed daily suggestion. The route owns the reveal/approval gate.

        The content blob is re-validated on the way through, which also drops the
        review-only ``unverified_ingredients`` from the public copy.
        """
        content = DailyMealContent.model_validate(row.content)
        return await self._upsert(
            user_id,
            SaveSource.DAILY,
            str(row.id),
            meal_type=row.meal_type,
            name=content.name,
            description=content.description,
            ingredients=[item.model_dump() for item in content.ingredients],
            recipe=content.recipe,
            tags=[row.meal_type.value],
            cautioned_ingredients=[item.model_dump() for item in content.cautioned_ingredients],
            model=row.model,
            verdict=None,
        )

    async def save_lookup(self, user_id: UUID, payload: SaveFromLookup) -> tuple[SavedMeal, bool]:
        """Store an assessed dish; the schema already normalized and capped it."""
        return await self._upsert(
            user_id,
            SaveSource.LOOKUP,
            str(payload.lookup_id),
            meal_type=None,
            name=await self._unique_name(user_id, payload.dish),
            description=payload.description,
            ingredients=[item.model_dump() for item in payload.ingredients],
            recipe=payload.recipe,
            recipe_model=payload.recipe_model,
            tags=[SavedMealTag.DISH_CHECK.value],
            cautioned_ingredients=[],
            model=payload.model,
            verdict=payload.verdict,
        )

    async def save(
        self,
        user_id: UUID,
        payload: SaveRequest,
        *,
        meals: MealService,
        daily: DailyService,
    ) -> tuple[SavedMeal, bool]:
        """Save a meal from any source; the flag is False when it was already saved.

        Idempotent per (source, source row): re-liking returns the earlier snapshot
        even if the source has changed since. A lookup save is keyed on the client's
        per-result ``lookup_id``, so each assessment result saves as its own row and
        only a retry of the same result is idempotent. The per-user cap raises; it is
        an abuse bound, not something a real collection should reach.
        """
        if isinstance(payload, SaveFromLookup):
            source_key = str(payload.lookup_id)
        else:
            source_key = str(payload.source_id)

        existing = await self.find(user_id, payload.source, source_key)
        if existing is not None:
            return existing, False

        if await self.count_for(user_id) >= settings.saved_meals_cap:
            raise SaveLimitReached(settings.saved_meals_cap)

        if isinstance(payload, SaveFromLookup):
            return await self.save_lookup(user_id, payload)
        if payload.source is SaveSource.CURATED:
            meal = await meals.get_approved(payload.source_id)
            if meal is None:
                raise SavedMealNotFound
            return await self.save_curated(user_id, meal)

        suggestion = await daily.get(payload.source_id)
        if (
            suggestion is None
            or suggestion.approval_status is not ApprovalStatus.APPROVED
            or datetime.now(UTC) < suggestion.reveal_at
        ):
            # Unknown, unapproved, and unrevealed are indistinguishable on purpose:
            # a saved id must not become a probe for tomorrow's board.
            raise SavedMealNotFound
        return await self.save_daily(user_id, suggestion)

    async def generate_recipe(
        self,
        user_id: UUID,
        save_id: UUID,
        *,
        agent: RecipeAgent,
        resolved: RequestLLM,
    ) -> SavedRecipeResponse:
        """Write a recipe for a saved copy that has none, and persist it on the row.

        Lazy by design: recipes cost a model call, so one is only written when its
        owner asks, from the snapshot's current (possibly user-edited) ingredients.
        Idempotent: a row that already has a recipe returns unchanged, uncharged.
        """
        row = await self.get(user_id, save_id)
        if row is None:
            raise SavedMealNotFound
        if row.recipe:
            # A recipe that came with the snapshot has no recipe_model of its own;
            # the save's producer is then the closest honest provenance.
            return SavedRecipeResponse(
                meal=saved_detail(row),
                recipe_model=row.recipe_model or row.model,
                usage=LLMUsage(),
            )

        await resolved.charge()
        generation = await agent.run(
            name=row.name,
            description=row.description,
            ingredients=[ProposedIngredient.model_validate(item) for item in row.ingredients],
            cautions=[
                CautionedIngredient.model_validate(item) for item in row.cautioned_ingredients
            ],
        )
        saved = await self.set_recipe(user_id, save_id, generation.steps, generation.model)
        if saved is None:
            # The save was deleted while the model wrote; nothing was persisted, so
            # a response claiming a recipe exists would be a lie.
            raise SavedMealNotFound
        return SavedRecipeResponse(
            meal=saved_detail(saved),
            recipe_model=saved.recipe_model or saved.model,
            usage=generation.usage,
        )

    async def update(self, row: SavedMeal, fields: SavedMealUpdate) -> SavedMeal:
        """Apply an edit to the user's copy and stamp it as user-modified."""
        row.name = fields.name
        row.description = fields.description
        row.ingredients = [item.model_dump() for item in fields.ingredients]
        row.recipe = fields.recipe
        row.tags = fields.tags
        if row.edited_at is None:
            row.edited_at = datetime.now(UTC)
        await self._session.flush()
        return row

    async def delete(self, row: SavedMeal) -> None:
        await self._session.delete(row)
        await self._session.flush()

    async def _unique_name(self, user_id: UUID, name: str) -> str:
        """The name, suffixed " (n)" if the user already saved one by that name.

        Lookup saves are keyed on the per-result id, so nothing stops two saves
        from sharing a name; this keeps the collection tellable-apart instead.
        The base is trimmed so a suffixed name still fits the dish cap.
        """
        result = await self._session.execute(
            select(SavedMeal.name).where(SavedMeal.user_id == user_id)
        )
        taken = {existing.casefold() for existing in result.scalars()}
        if name.casefold() not in taken:
            return name
        for n in range(1, len(taken) + 2):
            suffix = f" ({n})"
            candidate = name[: MAX_DISH_CHARS - len(suffix)].rstrip() + suffix
            if candidate.casefold() not in taken:
                return candidate
        raise AssertionError("unreachable: more suffixes than existing saves")

    async def _upsert(
        self, user_id: UUID, source: SaveSource, source_key: str, **content: Any
    ) -> tuple[SavedMeal, bool]:
        """Insert the snapshot, or return the existing save on a duplicate.

        ON CONFLICT DO NOTHING keeps a duplicate from surfacing as an
        IntegrityError, which would poison the request-scoped session.
        """
        stmt = (
            pg_insert(SavedMeal)
            .values(user_id=user_id, source=source, source_key=source_key, **content)
            .on_conflict_do_nothing(constraint="uq_saved_meals_user_source")
            .returning(SavedMeal)
        )
        created = (await self._session.execute(stmt)).scalar_one_or_none()
        if created is not None:
            return created, True
        existing = await self.find(user_id, source, source_key)
        assert existing is not None  # the conflict row, visible in READ COMMITTED
        return existing, False


def saved_card(row: SavedMeal) -> SavedMealCard:
    """Shape a saved row into the lean card the profile shelf lists."""
    return SavedMealCard(
        id=row.id,
        source=row.source,
        source_key=row.source_key,
        meal_type=row.meal_type,
        name=row.name,
        description=row.description,
        tags=list(row.tags),
        verdict=row.verdict,
        edited_at=row.edited_at,
        created_at=row.created_at,
        has_recipe=bool(row.recipe),
    )


def saved_detail(row: SavedMeal) -> SavedMealDetail:
    """Shape a saved row into the full view its detail and edit surfaces render."""
    return SavedMealDetail(
        **saved_card(row).model_dump(),
        ingredients=[ProposedIngredient.model_validate(item) for item in row.ingredients],
        recipe=row.recipe,
        cautioned_ingredients=[
            CautionedIngredient.model_validate(item) for item in row.cautioned_ingredients
        ],
        model=row.model,
        recipe_model=row.recipe_model,
    )
