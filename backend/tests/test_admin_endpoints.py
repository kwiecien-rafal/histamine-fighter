"""Endpoint tests for the admin gate: login, then review/approve/reject meals.

These run against the test database (the conftest ``client`` shares the rolled-back
session), so they cover the real auth path end to end: a token is minted, the
dependency re-reads the admin, and the review service flips status on real rows.
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.config import settings
from app.core.ratelimit import limiter
from app.core.security import create_access_token, hash_password
from app.embeddings import EMBEDDING_DIM
from app.enums import ApprovalStatus, MealType
from app.models import CuratedMeal
from app.models.admin_user import AdminUser

_EMAIL = "admin@example.com"
_PASSWORD = "supersecret"
_ZERO_VECTOR = [0.0] * EMBEDDING_DIM


async def _add_admin(
    session: AsyncSession, *, email: str = _EMAIL, password: str = _PASSWORD
) -> None:
    session.add(AdminUser(email=email, password_hash=hash_password(password)))
    await session.flush()


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


def _auth_header(email: str = _EMAIL, *, token_version: int = 1) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(email, token_version=token_version)}"}


# --- POST /admin/auth/login -------------------------------------------------------


async def test_login_returns_a_bearer_token(client: AsyncClient, session: AsyncSession) -> None:
    await _add_admin(session)

    resp = await client.post("/admin/auth/login", json={"email": _EMAIL, "password": _PASSWORD})

    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_login_is_case_insensitive_on_email(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)

    resp = await client.post(
        "/admin/auth/login", json={"email": "Admin@Example.com", "password": _PASSWORD}
    )

    assert resp.status_code == 200


async def test_login_with_wrong_password_is_401(client: AsyncClient, session: AsyncSession) -> None:
    await _add_admin(session)

    resp = await client.post("/admin/auth/login", json={"email": _EMAIL, "password": "nope"})

    assert resp.status_code == 401


async def test_login_with_unknown_email_is_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/admin/auth/login", json={"email": "ghost@example.com", "password": _PASSWORD}
    )

    assert resp.status_code == 401


async def test_successful_login_is_logged(client: AsyncClient, session: AsyncSession) -> None:
    await _add_admin(session)

    with capture_logs() as logs:
        await client.post("/admin/auth/login", json={"email": _EMAIL, "password": _PASSWORD})

    success = next(entry for entry in logs if entry["event"] == "admin.login.success")
    assert success["email"] == _EMAIL


async def test_failed_login_is_logged_with_the_attempted_email(client: AsyncClient) -> None:
    with capture_logs() as logs:
        await client.post("/admin/auth/login", json={"email": _EMAIL, "password": "nope"})

    failure = next(entry for entry in logs if entry["event"] == "admin.login.failed")
    assert failure["email"] == _EMAIL
    # The password must never reach the logs.
    assert "nope" not in str(logs)


async def test_login_is_rate_limited(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _add_admin(session)
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1)
    limiter.reset()
    limiter.enabled = True

    first = await client.post("/admin/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    second = await client.post("/admin/auth/login", json={"email": _EMAIL, "password": _PASSWORD})

    assert first.status_code == 200
    assert second.status_code == 429


# --- auth gate --------------------------------------------------------------------


async def test_list_meals_without_a_token_is_401(client: AsyncClient) -> None:
    resp = await client.get("/admin/meals")
    assert resp.status_code == 401


async def test_list_meals_with_a_garbage_token_is_401(client: AsyncClient) -> None:
    resp = await client.get("/admin/meals", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


async def test_token_for_a_deleted_admin_is_401(client: AsyncClient) -> None:
    # A well-signed token whose subject has no account must not pass the gate.
    resp = await client.get("/admin/meals", headers=_auth_header("ghost@example.com"))
    assert resp.status_code == 401


async def test_token_with_a_stale_version_is_401(
    client: AsyncClient, session: AsyncSession
) -> None:
    # A token minted under an earlier password (lower version) is revoked by a reset.
    await _add_admin(session)

    resp = await client.get("/admin/meals", headers=_auth_header(token_version=0))

    assert resp.status_code == 401


# --- GET /admin/meals -------------------------------------------------------------


async def test_list_pending_meals_returns_the_queue_with_review_detail(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)
    await _add_meal(session, name="Pending salad", approval_status=ApprovalStatus.PENDING)
    await _add_meal(session, name="Approved bake", approval_status=ApprovalStatus.APPROVED)

    resp = await client.get("/admin/meals", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert [meal["name"] for meal in body] == ["Pending salad"]
    meal = body[0]
    # The reviewer sees the ingredients and the trace, not just a title.
    assert meal["ingredients"] == [{"name": "courgette", "category": "vegetable"}]
    assert meal["reasoning_trace"][0]["kind"] == "verify"
    assert meal["approval_status"] == "pending"


async def test_list_meals_filters_by_status(client: AsyncClient, session: AsyncSession) -> None:
    await _add_admin(session)
    await _add_meal(session, name="Pending salad", approval_status=ApprovalStatus.PENDING)
    await _add_meal(session, name="Approved bake", approval_status=ApprovalStatus.APPROVED)

    resp = await client.get("/admin/meals", params={"status": "approved"}, headers=_auth_header())

    assert resp.status_code == 200
    assert [meal["name"] for meal in resp.json()] == ["Approved bake"]


async def test_list_meals_is_oldest_first(client: AsyncClient, session: AsyncSession) -> None:
    await _add_admin(session)
    # Insert newest first to prove the order is by created_at, not insertion.
    await _add_meal(session, name="Newer", created_at=datetime(2026, 1, 2, tzinfo=UTC))
    await _add_meal(session, name="Older", created_at=datetime(2026, 1, 1, tzinfo=UTC))

    resp = await client.get("/admin/meals", headers=_auth_header())

    assert resp.status_code == 200
    assert [meal["name"] for meal in resp.json()] == ["Older", "Newer"]


async def test_list_meals_paginates_with_limit_and_offset(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)
    await _add_meal(session, name="First", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    await _add_meal(session, name="Second", created_at=datetime(2026, 1, 2, tzinfo=UTC))
    await _add_meal(session, name="Third", created_at=datetime(2026, 1, 3, tzinfo=UTC))

    resp = await client.get(
        "/admin/meals", params={"limit": 1, "offset": 1}, headers=_auth_header()
    )

    assert resp.status_code == 200
    assert [meal["name"] for meal in resp.json()] == ["Second"]


async def test_list_meals_rejects_an_out_of_range_limit(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)

    resp = await client.get("/admin/meals", params={"limit": 0}, headers=_auth_header())

    assert resp.status_code == 422


# --- PATCH /admin/meals/{id}/approve | reject -------------------------------------


async def test_approve_flips_status_and_stamps_the_actor(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)
    meal = await _add_meal(session)

    resp = await client.patch(f"/admin/meals/{meal.id}/approve", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "approved"
    assert body["approved_by"] == _EMAIL
    assert body["approved_at"] is not None

    # The test session deliberately never commits, so flush the endpoint's pending
    # UPDATE before reading the row back from the database.
    await session.flush()
    await session.refresh(meal)
    assert meal.approval_status is ApprovalStatus.APPROVED
    assert meal.approved_by == _EMAIL
    assert meal.approved_at is not None


async def test_reject_flips_status_and_clears_any_approval_stamp(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)
    meal = await _add_meal(session, approval_status=ApprovalStatus.APPROVED)
    meal.approved_by = "someone@example.com"
    await session.flush()

    resp = await client.patch(f"/admin/meals/{meal.id}/reject", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "rejected"
    assert body["approved_by"] is None
    assert body["approved_at"] is None


async def test_approve_unknown_meal_is_404(client: AsyncClient, session: AsyncSession) -> None:
    await _add_admin(session)

    resp = await client.patch(
        "/admin/meals/00000000-0000-0000-0000-000000000000/approve", headers=_auth_header()
    )

    assert resp.status_code == 404


async def test_approve_without_a_token_is_401(client: AsyncClient, session: AsyncSession) -> None:
    meal = await _add_meal(session)

    resp = await client.patch(f"/admin/meals/{meal.id}/approve")

    assert resp.status_code == 401
