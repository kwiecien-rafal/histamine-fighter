"""Tests for vector retrieval over the curated meal pool (DB + fake embedder)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ApprovalStatus, MealType
from app.models import CuratedMeal
from app.models.curated_meal import meal_embedding_text
from app.schemas.admin import AdminMealUpdate
from app.schemas.meal import ComposedMeal, ProposedIngredient, TraceEvent
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


async def test_exclude_matches_a_longer_term_against_a_shorter_ingredient(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    # The avoided term is "tomato sauce" but the meal lists plain "tomato": the
    # token subset still drops it. Exact-string matching (the old behaviour) missed
    # this and served the meal back with a verified badge.
    await _add_meal(
        session,
        fake_embedder,
        name="Pasta pomodoro",
        description="pasta with a simple tomato base and basil",
        ingredients=[{"name": "Tomato", "category": "nightshade"}],
    )
    await _add_meal(
        session,
        fake_embedder,
        name="Herb pasta",
        description="pasta with olive oil and fresh basil",
        ingredients=[{"name": "Basil", "category": "herb"}],
    )
    await session.flush()

    service = MealService(session, fake_embedder, min_similarity=0.0)
    results = await service.search("simple pasta with basil", exclude=["tomato sauce"])

    assert [match.meal.name for match in results] == ["Herb pasta"]


async def test_exclude_matches_a_shorter_term_against_a_longer_ingredient(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    # Avoiding "wine" drops a meal listing "red wine": the term's tokens are a
    # subset of the ingredient's.
    await _add_meal(
        session,
        fake_embedder,
        name="Coq au vin",
        description="braised chicken in a red wine reduction",
        ingredients=[{"name": "Red Wine", "category": "alcohol"}],
    )
    await _add_meal(
        session,
        fake_embedder,
        name="Roast chicken",
        description="braised chicken with thyme and garlic",
        ingredients=[{"name": "Chicken", "category": "poultry"}],
    )
    await session.flush()

    service = MealService(session, fake_embedder, min_similarity=0.0)
    results = await service.search("braised chicken with herbs", exclude=["wine"])

    assert [match.meal.name for match in results] == ["Roast chicken"]


async def test_exclude_does_not_match_a_substring_inside_a_single_token(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    # "egg" must not drop "eggplant": they are distinct single tokens, so the
    # token-set rule avoids the substring false positive.
    await _add_meal(
        session,
        fake_embedder,
        name="Roasted eggplant",
        description="roasted eggplant with olive oil and herbs",
        ingredients=[{"name": "Eggplant", "category": "nightshade"}],
    )
    await session.flush()

    service = MealService(session, fake_embedder, min_similarity=0.0)
    results = await service.search("roasted eggplant with herbs", exclude=["egg"])

    assert [match.meal.name for match in results] == ["Roasted eggplant"]


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


# --- store_pending ----------------------------------------------------------------


async def test_store_pending_persists_a_pending_row_with_trace_and_embedding(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    meal = ComposedMeal(
        name="Courgette ribbon salad",
        meal_type=MealType.DINNER,
        description="raw courgette ribbons with olive oil and fresh herbs",
        ingredients=[ProposedIngredient(name="courgette", category="vegetable")],
        recipe=["Peel into ribbons."],
        tags=["fresh"],
        unverified_ingredients=["mystery spice"],
        model="fake/test",
        reasoning_trace=[TraceEvent(kind="verify", text="cleared the index")],
    )

    row = await MealService(session, fake_embedder).store_pending(meal)
    await session.flush()

    assert row.approval_status is ApprovalStatus.PENDING
    assert row.name == "Courgette ribbon salad"
    assert row.unverified_ingredients == ["mystery spice"]
    assert [event["text"] for event in row.reasoning_trace] == ["cleared the index"]
    assert row.embedding  # the meal was embedded for later retrieval


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


# --- apply_edit -------------------------------------------------------------------


async def _add_pending(
    session: AsyncSession, embedder: FakeEmbedder, *, name: str, description: str, tags: list[str]
) -> CuratedMeal:
    vector = (await embedder.embed_documents([meal_embedding_text(name, description, tags)]))[0]
    meal = CuratedMeal(
        name=name,
        meal_type=MealType.DINNER,
        description=description,
        ingredients=[{"name": "courgette", "category": "vegetable"}],
        recipe=["Cook it."],
        tags=tags,
        model="fake/test",
        reasoning_trace=[],
        approval_status=ApprovalStatus.PENDING,
        embedding=vector,
    )
    session.add(meal)
    await session.flush()
    return meal


def _update(meal: CuratedMeal, **overrides: object) -> AdminMealUpdate:
    base: dict[str, object] = {
        "name": meal.name,
        "description": meal.description,
        "ingredients": [{"name": "courgette", "category": "vegetable"}],
        "recipe": ["Cook it."],
        "tags": list(meal.tags),
    }
    base.update(overrides)
    return AdminMealUpdate.model_validate(base)


async def test_apply_edit_reembeds_when_text_changes(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    meal = await _add_pending(
        session, fake_embedder, name="Old name", description="old description", tags=["a"]
    )
    before = list(meal.embedding)

    await MealService(session, fake_embedder).apply_edit(
        meal, _update(meal, name="A brand new name"), unverified=[]
    )

    assert meal.name == "A brand new name"
    assert list(meal.embedding) != before  # retrieval text changed, so it re-embedded


async def test_apply_edit_skips_reembed_when_only_ingredients_change(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    meal = await _add_pending(
        session, fake_embedder, name="Stable name", description="stable description", tags=["a"]
    )
    before = list(meal.embedding)

    await MealService(session, fake_embedder).apply_edit(
        meal,
        _update(meal, ingredients=[{"name": "buckwheat", "category": "grain"}]),
        unverified=["buckwheat"],
    )

    assert [item["name"] for item in meal.ingredients] == ["buckwheat"]
    assert meal.unverified_ingredients == ["buckwheat"]
    assert list(meal.embedding) == before  # text unchanged, so the embedding is untouched
