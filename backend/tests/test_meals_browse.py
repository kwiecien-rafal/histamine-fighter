"""Endpoint tests for the public curated browse: GET /api/v1/meals and /{id}.

DB-backed (the conftest ``client`` shares the rolled-back session): a plain read of the
approved pool, no agent and no LLM. The list serves lean cards plus a total; the detail
serves one full meal. Rows carry a zero embedding, which both reads defer and never read.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import EMBEDDING_DIM
from app.enums import ApprovalStatus, MealType
from app.models import CuratedMeal

_ZERO_VECTOR = [0.0] * EMBEDDING_DIM
# Distinguishes "caller left recipe unset" (use the stub) from "caller passed None"
# (a meal with no recipe), which a plain None default could not tell apart.
_UNSET: Any = object()


async def _add_meal(
    session: AsyncSession,
    *,
    name: str,
    meal_type: MealType = MealType.LUNCH,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
    recipe: list[str] | None = _UNSET,
    trace: list[dict[str, str]] | None = None,
    created_at: datetime | None = None,
) -> CuratedMeal:
    meal = CuratedMeal(
        name=name,
        meal_type=meal_type,
        description="raw courgette ribbons with olive oil and fresh herbs",
        ingredients=[{"name": "courgette", "category": "vegetable"}],
        recipe=["Peel into ribbons."] if recipe is _UNSET else recipe,
        tags=["fresh"],
        unverified_ingredients=["mystery spice"],
        model="fake/test",
        reasoning_trace=trace if trace is not None else [{"kind": "verify", "text": "cleared"}],
        approval_status=approval_status,
        embedding=_ZERO_VECTOR,
    )
    # now() is transaction-scoped in Postgres, so set created_at explicitly when a test
    # needs a deterministic order.
    if created_at is not None:
        meal.created_at = created_at
    session.add(meal)
    await session.flush()
    return meal


async def test_lists_only_approved_meals(client: AsyncClient, session: AsyncSession) -> None:
    await _add_meal(session, name="Approved salad", approval_status=ApprovalStatus.APPROVED)
    await _add_meal(session, name="Pending bake", approval_status=ApprovalStatus.PENDING)
    await _add_meal(session, name="Rejected stew", approval_status=ApprovalStatus.REJECTED)

    resp = await client.get("/api/v1/meals")

    assert resp.status_code == 200
    body = resp.json()
    assert [meal["name"] for meal in body["items"]] == ["Approved salad"]
    assert body["total"] == 1


async def test_list_card_is_lean_with_content_flags(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_meal(
        session,
        name="Approved salad",
        recipe=["Peel into ribbons."],
        trace=[
            {"kind": "draft", "text": "thinking out loud"},
            {"kind": "verify", "text": "All ingredients cleared the index."},
        ],
    )

    card = (await client.get("/api/v1/meals")).json()["items"][0]

    assert card["model"] == "fake/test"
    assert card["meal_type"] == "lunch"
    # A code-authored step survives the public filter, so the card flags a watchable trace.
    assert card["has_recipe"] is True
    assert card["has_trace"] is True
    # The heavy fields live on the detail, not the list card.
    for field in ("ingredients", "recipe", "trace", "unverified_ingredients"):
        assert field not in card


async def test_list_card_flags_absent_content(client: AsyncClient, session: AsyncSession) -> None:
    # A meal with no recipe and only the model's own prose has nothing public to show.
    await _add_meal(
        session, name="Bare meal", recipe=None, trace=[{"kind": "draft", "text": "just musing"}]
    )

    card = (await client.get("/api/v1/meals")).json()["items"][0]

    assert card["has_recipe"] is False
    assert card["has_trace"] is False


async def test_filters_by_meal_type(client: AsyncClient, session: AsyncSession) -> None:
    await _add_meal(session, name="Lunch salad", meal_type=MealType.LUNCH)
    await _add_meal(session, name="Breakfast oats", meal_type=MealType.BREAKFAST)

    body = (await client.get("/api/v1/meals", params={"meal_type": "breakfast"})).json()

    assert [meal["name"] for meal in body["items"]] == ["Breakfast oats"]
    assert body["total"] == 1


async def test_lists_newest_first_and_paginates(client: AsyncClient, session: AsyncSession) -> None:
    await _add_meal(session, name="Oldest", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    await _add_meal(session, name="Newest", created_at=datetime(2026, 1, 3, tzinfo=UTC))
    await _add_meal(session, name="Middle", created_at=datetime(2026, 1, 2, tzinfo=UTC))

    page = (await client.get("/api/v1/meals", params={"limit": 2})).json()
    assert [meal["name"] for meal in page["items"]] == ["Newest", "Middle"]
    # The total counts the whole pool, not just the page, so the client can keep paging.
    assert page["total"] == 3

    rest = (await client.get("/api/v1/meals", params={"limit": 2, "offset": 2})).json()
    assert [meal["name"] for meal in rest["items"]] == ["Oldest"]
    assert rest["total"] == 3


async def test_sets_cache_control(client: AsyncClient, session: AsyncSession) -> None:
    await _add_meal(session, name="Approved salad")

    resp = await client.get("/api/v1/meals")

    assert resp.headers["cache-control"] == "public, max-age=60"


async def test_detail_returns_full_meal_with_filtered_trace(
    client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(
        session,
        name="Approved salad",
        trace=[
            {"kind": "draft", "text": "thinking out loud"},
            {"kind": "verify", "text": "All ingredients cleared the index."},
        ],
    )

    resp = await client.get(f"/api/v1/meals/{meal.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(meal.id)
    assert body["ingredients"] == [{"name": "courgette", "category": "vegetable"}]
    assert body["recipe"] == ["Peel into ribbons."]
    # The model's own prose (draft) is filtered out, exactly as on the daily board.
    assert [event["kind"] for event in body["trace"]] == ["verify"]
    # Review-only context never reaches the public detail.
    assert "unverified_ingredients" not in body
    assert resp.headers["cache-control"] == "public, max-age=60"


async def test_detail_404_for_pending(client: AsyncClient, session: AsyncSession) -> None:
    meal = await _add_meal(session, name="Pending bake", approval_status=ApprovalStatus.PENDING)

    resp = await client.get(f"/api/v1/meals/{meal.id}")

    assert resp.status_code == 404


async def test_detail_404_for_rejected(client: AsyncClient, session: AsyncSession) -> None:
    meal = await _add_meal(session, name="Rejected stew", approval_status=ApprovalStatus.REJECTED)

    resp = await client.get(f"/api/v1/meals/{meal.id}")

    assert resp.status_code == 404


async def test_detail_404_for_unknown_id(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/meals/{uuid4()}")

    assert resp.status_code == 404
