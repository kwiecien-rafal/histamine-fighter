"""Endpoint tests for the curated-meal review gate: list, approve, reject.

These run against the test database (the conftest ``authenticated_client`` shares the
rolled-back session and carries the session cookie), so they cover the real path end
to end: the gate re-reads the user, and the review service flips status on real rows.
Login, logout, and the auth/role gate itself live in test_auth_endpoints.
"""

from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import EMBEDDING_DIM
from app.enums import ApprovalStatus, MealType
from app.models import CuratedMeal
from app.models.user import User

_ZERO_VECTOR = [0.0] * EMBEDDING_DIM


async def _add_meal(
    session: AsyncSession,
    *,
    name: str = "Courgette ribbon salad",
    approval_status: ApprovalStatus = ApprovalStatus.PENDING,
    created_at: datetime | None = None,
) -> CuratedMeal:
    meal = CuratedMeal(
        name=name,
        meal_type=MealType.LUNCH,
        description="raw courgette ribbons with olive oil and fresh herbs",
        ingredients=[{"name": "courgette", "category": "vegetable"}],
        recipe=["Peel into ribbons.", "Toss with oil and herbs."],
        tags=["fresh"],
        model="fake/test",
        reasoning_trace=[{"kind": "verify", "text": "All ingredients cleared the index."}],
        approval_status=approval_status,
        embedding=_ZERO_VECTOR,
    )
    # now() is transaction-scoped in Postgres, so same-transaction rows share a
    # created_at; set it explicitly when a test needs a deterministic order.
    if created_at is not None:
        meal.created_at = created_at
    session.add(meal)
    await session.flush()
    return meal


# --- GET /admin/meals -------------------------------------------------------------


async def test_list_pending_meals_returns_the_queue_with_review_detail(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _add_meal(session, name="Pending salad", approval_status=ApprovalStatus.PENDING)
    await _add_meal(session, name="Approved bake", approval_status=ApprovalStatus.APPROVED)

    resp = await authenticated_client.get("/admin/meals")

    assert resp.status_code == 200
    body = resp.json()
    assert [meal["name"] for meal in body] == ["Pending salad"]
    meal = body[0]
    # The reviewer sees the ingredients and the trace, not just a title.
    assert meal["ingredients"] == [{"name": "courgette", "category": "vegetable"}]
    assert meal["reasoning_trace"][0]["kind"] == "verify"
    assert meal["approval_status"] == "pending"


async def test_list_meals_filters_by_status(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _add_meal(session, name="Pending salad", approval_status=ApprovalStatus.PENDING)
    await _add_meal(session, name="Approved bake", approval_status=ApprovalStatus.APPROVED)

    resp = await authenticated_client.get("/admin/meals", params={"status": "approved"})

    assert resp.status_code == 200
    assert [meal["name"] for meal in resp.json()] == ["Approved bake"]


async def test_list_meals_is_oldest_first(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    # Insert newest first to prove the order is by created_at, not insertion.
    await _add_meal(session, name="Newer", created_at=datetime(2026, 1, 2, tzinfo=UTC))
    await _add_meal(session, name="Older", created_at=datetime(2026, 1, 1, tzinfo=UTC))

    resp = await authenticated_client.get("/admin/meals")

    assert resp.status_code == 200
    assert [meal["name"] for meal in resp.json()] == ["Older", "Newer"]


async def test_list_meals_paginates_with_limit_and_offset(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _add_meal(session, name="First", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    await _add_meal(session, name="Second", created_at=datetime(2026, 1, 2, tzinfo=UTC))
    await _add_meal(session, name="Third", created_at=datetime(2026, 1, 3, tzinfo=UTC))

    resp = await authenticated_client.get("/admin/meals", params={"limit": 1, "offset": 1})

    assert resp.status_code == 200
    assert [meal["name"] for meal in resp.json()] == ["Second"]


async def test_list_meals_rejects_an_out_of_range_limit(
    authenticated_client: AsyncClient,
) -> None:
    resp = await authenticated_client.get("/admin/meals", params={"limit": 0})

    assert resp.status_code == 422


# --- PATCH /admin/meals/{id}/approve | reject -------------------------------------


async def test_approve_flips_status_and_stamps_the_actor(
    authenticated_client: AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    meal = await _add_meal(session)

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}/approve")

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "approved"
    assert body["approved_by"] == admin_user.email
    assert body["approved_at"] is not None

    # The test session deliberately never commits, so flush the endpoint's pending
    # UPDATE before reading the row back from the database.
    await session.flush()
    await session.refresh(meal)
    assert meal.approval_status is ApprovalStatus.APPROVED
    assert meal.approved_by == admin_user.email
    assert meal.approved_at is not None


async def test_reject_flips_status_and_clears_any_approval_stamp(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session, approval_status=ApprovalStatus.APPROVED)
    meal.approved_by = "someone@example.com"
    await session.flush()

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}/reject")

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "rejected"
    assert body["approved_by"] is None
    assert body["approved_at"] is None


async def test_approve_unknown_meal_is_404(authenticated_client: AsyncClient) -> None:
    resp = await authenticated_client.patch(
        "/admin/meals/00000000-0000-0000-0000-000000000000/approve"
    )

    assert resp.status_code == 404


async def test_approve_without_a_session_is_401(client: AsyncClient, session: AsyncSession) -> None:
    meal = await _add_meal(session)

    resp = await client.patch(f"/admin/meals/{meal.id}/approve")

    assert resp.status_code == 401
