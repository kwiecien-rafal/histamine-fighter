"""Tests for vector retrieval over the curated meal pool (DB + fake embedder)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ApprovalStatus, MealType
from app.models import CuratedMeal
from app.models.curated_meal import meal_embedding_text
from app.services.meal_service import MealService
from tests.fakes import FakeEmbedder


async def _add_meal(
    session: AsyncSession,
    embedder: FakeEmbedder,
    *,
    name: str,
    description: str,
    meal_type: MealType = MealType.DINNER,
    tags: list[str] | None = None,
    ingredients: list[dict[str, str | None]] | None = None,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
) -> None:
    tags = tags or []
    vector = (await embedder.embed_documents([meal_embedding_text(name, description, tags)]))[0]
    session.add(
        CuratedMeal(
            name=name,
            meal_type=meal_type,
            description=description,
            ingredients=ingredients or [],
            recipe=None,
            tags=tags,
            model="fake/test",
            reasoning_trace=[],
            approval_status=approval_status,
            embedding=vector,
        )
    )


async def test_search_ranks_relevant_meal_first(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _add_meal(
        session,
        fake_embedder,
        name="Courgette ribbon salad",
        description="raw courgette ribbons with olive oil and fresh herbs",
    )
    await _add_meal(
        session,
        fake_embedder,
        name="Buckwheat porridge",
        description="warm buckwheat with pear and a drizzle of maple",
    )
    await session.flush()

    # Floor disabled: this test is about ranking, and the bag-of-words fake
    # scores lower than the real model the default floor is tuned for.
    service = MealService(session, fake_embedder, min_similarity=0.0)
    results = await service.search("fresh courgette ribbons with herbs")

    assert results
    assert results[0].meal.name == "Courgette ribbon salad"
    assert results[0].similarity >= results[-1].similarity


async def test_search_returns_only_approved(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _add_meal(
        session,
        fake_embedder,
        name="Approved courgette bake",
        description="baked courgette with olive oil and herbs",
        approval_status=ApprovalStatus.APPROVED,
    )
    await _add_meal(
        session,
        fake_embedder,
        name="Pending courgette bake",
        description="baked courgette with olive oil and herbs",
        approval_status=ApprovalStatus.PENDING,
    )
    await _add_meal(
        session,
        fake_embedder,
        name="Rejected courgette bake",
        description="baked courgette with olive oil and herbs",
        approval_status=ApprovalStatus.REJECTED,
    )
    await session.flush()

    service = MealService(session, fake_embedder, min_similarity=0.0)
    results = await service.search("baked courgette with herbs")

    assert [match.meal.name for match in results] == ["Approved courgette bake"]


async def test_search_filters_by_meal_type(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _add_meal(
        session,
        fake_embedder,
        name="Pear buckwheat porridge",
        description="warm buckwheat porridge with pear",
        meal_type=MealType.BREAKFAST,
    )
    await _add_meal(
        session,
        fake_embedder,
        name="Buckwheat pear risotto",
        description="savoury buckwheat with pear",
        meal_type=MealType.DINNER,
    )
    await session.flush()

    service = MealService(session, fake_embedder, min_similarity=0.0)
    results = await service.search("buckwheat with pear", meal_type=MealType.BREAKFAST)

    assert [match.meal.name for match in results] == ["Pear buckwheat porridge"]


async def test_search_excludes_meals_with_listed_ingredient_or_category(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _add_meal(
        session,
        fake_embedder,
        name="Roast pepper salad",
        description="roast peppers with olive oil and herbs",
        ingredients=[{"name": "Tomato", "category": "nightshade"}],
    )
    await _add_meal(
        session,
        fake_embedder,
        name="Courgette pepper salad",
        description="roast peppers and courgette with olive oil and herbs",
        ingredients=[{"name": "Aged Parmesan", "category": "aged hard cheese"}],
    )
    await _add_meal(
        session,
        fake_embedder,
        name="Herb pepper plate",
        description="roast peppers with olive oil and herbs",
        ingredients=[{"name": "Courgette", "category": "vegetable"}],
    )
    await session.flush()

    service = MealService(session, fake_embedder, min_similarity=0.0)
    # "tomato" excludes by ingredient name; "aged hard cheese" by category.
    results = await service.search(
        "roast peppers with herbs", exclude=["tomato", "aged hard cheese"]
    )

    assert [match.meal.name for match in results] == ["Herb pepper plate"]


async def test_similarity_floor_drops_weak_matches(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _add_meal(
        session,
        fake_embedder,
        name="Courgette herb salad",
        description="raw courgette with fresh herbs and olive oil",
    )
    await _add_meal(
        session,
        fake_embedder,
        name="Carpentry workshop bowl",
        description="completely unrelated words about sawdust and sailing",
    )
    await session.flush()

    service = MealService(session, fake_embedder, min_similarity=0.5)
    results = await service.search("raw courgette with fresh herbs and olive oil")

    assert [match.meal.name for match in results] == ["Courgette herb salad"]


async def test_off_topic_query_returns_nothing(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _add_meal(
        session,
        fake_embedder,
        name="Courgette herb salad",
        description="raw courgette with fresh herbs and olive oil",
    )
    await session.flush()

    service = MealService(session, fake_embedder, min_similarity=0.5)
    assert await service.search("how do I tune a guitar") == []


async def test_empty_query_returns_nothing(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    assert await MealService(session, fake_embedder).search("   ") == []


async def test_search_respects_k(session: AsyncSession, fake_embedder: FakeEmbedder) -> None:
    for i in range(4):
        await _add_meal(
            session,
            fake_embedder,
            name=f"Courgette dish {i}",
            description="courgette with herbs and olive oil",
        )
    await session.flush()

    service = MealService(session, fake_embedder, min_similarity=0.0)
    results = await service.search("courgette with herbs", k=2)

    assert len(results) == 2


async def test_non_positive_k_raises(session: AsyncSession, fake_embedder: FakeEmbedder) -> None:
    """k=0 must fail loudly, not silently fall back to the default."""
    service = MealService(session, fake_embedder)
    with pytest.raises(ValueError, match="k must be >= 1"):
        await service.search("courgette", k=0)
    with pytest.raises(ValueError, match="k must be >= 1"):
        await service.search("courgette", k=-3)


async def test_oversized_query_raises(session: AsyncSession, fake_embedder: FakeEmbedder) -> None:
    """Too-long input is a caller error, not 'nothing relevant found'."""
    service = MealService(session, fake_embedder)
    with pytest.raises(ValueError, match="exceeds"):
        await service.search("x" * (MealService.max_query_length + 1))
