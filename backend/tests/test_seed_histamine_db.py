"""Tests for the seed loader's upsert behavior and the curated file's invariants."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HistamineIngredient
from app.scripts.seed_histamine_db import (
    SEED_FILE,
    IngredientSeedRow,
    load_rows,
    upsert_ingredients,
)


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


async def test_upsert_round_trips_the_category_flag(session: AsyncSession) -> None:
    rows = [IngredientSeedRow(name="Hard Cheese", is_category=True, sources=["x"])]

    await upsert_ingredients(session, rows)

    stored = await session.scalar(select(HistamineIngredient))
    assert stored is not None
    assert stored.is_category is True


def test_curated_seed_file_validates() -> None:
    assert load_rows(SEED_FILE)


def test_curated_seed_covers_the_parmesan_routing() -> None:
    # The category fallback only works if curation holds up its end: parmesan has
    # its own row, and the Hard Cheese umbrella row catches the descriptor the
    # dish-lookup prompt uses as its canonical example.
    rows = {row.name: row for row in load_rows(SEED_FILE)}

    assert rows["Parmesan"].compatibility is not None

    hard_cheese = rows["Hard Cheese"]
    assert hard_cheese.is_category
    assert "aged hard cheese" in hard_cheese.aliases
