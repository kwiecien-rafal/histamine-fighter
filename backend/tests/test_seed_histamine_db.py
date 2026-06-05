"""Tests for the seed loader's upsert behavior."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HistamineIngredient
from app.scripts.seed_histamine_db import IngredientSeedRow, upsert_ingredients


async def test_upsert_is_idempotent(session: AsyncSession) -> None:
    rows = [
        IngredientSeedRow(name="Tomato", compatibility="incompatible", sources=["x"]),
        IngredientSeedRow(name="Apple", compatibility="well_tolerated", sources=["x"]),
    ]

    assert await upsert_ingredients(session, rows) == (2, 0)
    # A second run updates the same rows instead of inserting duplicates.
    assert await upsert_ingredients(session, rows) == (0, 2)

    count = await session.scalar(select(func.count()).select_from(HistamineIngredient))
    assert count == 2
