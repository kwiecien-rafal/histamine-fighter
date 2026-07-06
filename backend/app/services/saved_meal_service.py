"""Service for per-user saved meals. Never commits; the request session owns that.

Saves are snapshots: curated and daily content is copied server-side from the
source row at save time (the route enforces the approval/reveal gates first), a
lookup save stores the client's normalized assessment. Inserts go through
``INSERT .. ON CONFLICT DO NOTHING`` so two racing taps on the same save button cannot
raise a unique violation into the request session; the loser reads the winner's
row. Two consequences worth knowing: a daily slot the admin regenerates keeps its
id but goes back to pending and off the board, so a save pointing at it simply
goes stale until re-approval; and re-saving a re-assessed dish returns the old
snapshot (delete and save again to refresh it).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import SavedMealTag, SaveSource
from app.models import CuratedMeal, DailySuggestion, SavedMeal
from app.schemas.daily import DailyMealContent
from app.schemas.meal import MAX_DISH_CHARS, normalize_dish_text
from app.schemas.saved import SavedMealUpdate, SaveFromLookup


def lookup_source_key(dish: str) -> str:
    """The dedupe key for a lookup save, derived server-side from the dish name."""
    return normalize_dish_text(dish, max_chars=MAX_DISH_CHARS).casefold()


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
            lookup_source_key(payload.dish),
            meal_type=None,
            name=payload.dish,
            description=payload.description,
            ingredients=[item.model_dump() for item in payload.ingredients],
            recipe=None,
            tags=[SavedMealTag.DISH_CHECK.value],
            cautioned_ingredients=[],
            model=payload.model,
            verdict=payload.verdict,
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
