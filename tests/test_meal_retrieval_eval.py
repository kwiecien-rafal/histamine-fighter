"""Similarity-floor eval for the curated meal pool, over the real embedder.

Guards the ``MealService`` floor that the verified-alternatives tier rests on: an
on-topic query must surface its pool meal above the floor (a real "from our
kitchen" pick), while an off-topic query must fall below it so the alternatives
head degrades to generation rather than a weak match. Mirrors the ingredient
retrieval eval and, like it, loads the real embedding model, so it is excluded
from the fast tier and run as its own step.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import Embedder, get_embedder
from app.enums import ApprovalStatus, MealType
from app.models import CuratedMeal
from app.models.curated_meal import meal_embedding_text
from app.services.meal_service import MealService

# Loads the real embedding model, so it is excluded from the fast tier.
pytestmark = pytest.mark.embeddings

_MEALS = [
    (
        "Courgette ribbon salad",
        "raw courgette ribbons with olive oil, lemon and fresh basil",
        MealType.LUNCH,
    ),
    (
        "Buckwheat porridge with pear",
        "warm buckwheat porridge topped with poached pear and maple syrup",
        MealType.BREAKFAST,
    ),
    (
        "Herb roasted chicken with rice",
        "roasted chicken thighs with thyme and garlic served over steamed rice",
        MealType.DINNER,
    ),
]


async def _seed(session: AsyncSession, embedder: Embedder) -> MealService:
    vectors = await embedder.embed_documents(
        [meal_embedding_text(name, description, []) for name, description, _ in _MEALS]
    )
    for (name, description, meal_type), vector in zip(_MEALS, vectors, strict=True):
        session.add(
            CuratedMeal(
                name=name,
                meal_type=meal_type,
                description=description,
                ingredients=[],
                recipe=None,
                tags=[],
                model="eval",
                reasoning_trace=[],
                approval_status=ApprovalStatus.APPROVED,
                embedding=vector,
            )
        )
    await session.flush()
    # Default floor: the value the verified tier actually ships with.
    return MealService(session, embedder)


async def test_on_topic_query_surfaces_its_meal_above_the_floor(session: AsyncSession) -> None:
    service = await _seed(session, get_embedder())

    matches = await service.search("courgette ribbons with olive oil and fresh basil")

    assert matches, "an on-topic query should clear the floor, not fall back to generation"
    assert matches[0].meal.name == "Courgette ribbon salad"


async def test_off_topic_query_falls_below_the_floor(session: AsyncSession) -> None:
    service = await _seed(session, get_embedder())

    # No pool meal is about car maintenance, so nothing should clear the floor.
    assert await service.search("how do I change a flat car tyre") == []
