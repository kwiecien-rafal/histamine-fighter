"""Endpoint tests for the daily board: public read and admin review.

These run against the test database (the conftest ``client`` shares the rolled-back
session). The public read derives "today" from the real UTC clock, so rows are
created for today with a reveal time deliberately in the past or future.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composer import ComposerExhausted
from app.api.admin.daily import _generation_lock
from app.api.v1.daily import _cache_max_age
from app.core.ratelimit import limiter
from app.core.security import create_access_token, hash_password
from app.db.session import get_session
from app.dependencies import get_composer_streamer
from app.enums import ApprovalStatus, MealType
from app.llm.errors import LLMInvocationError
from app.main import create_app
from app.models import DailySuggestion
from app.models.admin_user import AdminUser
from app.schemas.daily import LockedBoard, RevealedBoard
from app.schemas.meal import (
    ComposedMeal,
    MealStreamItem,
    ProposedIngredient,
    TraceEvent,
    TraceStreamItem,
)

_EMAIL = "admin@example.com"
_PASSWORD = "supersecret"


def _content(name: str, *, unverified: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": "raw courgette ribbons with olive oil and fresh herbs",
        "ingredients": [{"name": "courgette", "category": "vegetable"}],
        "recipe": ["Peel into ribbons.", "Toss with oil and herbs."],
        "tags": ["fresh"],
        "unverified_ingredients": unverified or [],
    }


async def _add_admin(session: AsyncSession) -> None:
    session.add(AdminUser(email=_EMAIL, password_hash=hash_password(_PASSWORD)))
    await session.flush()


async def _add_suggestion(
    session: AsyncSession,
    *,
    meal_type: MealType = MealType.BREAKFAST,
    reveal_at: datetime,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
    name: str = "Courgette ribbon salad",
    unverified: list[str] | None = None,
) -> DailySuggestion:
    row = DailySuggestion(
        suggestion_date=reveal_at.date(),
        meal_type=meal_type,
        content=_content(name, unverified=unverified),
        model="fake/test",
        reasoning_trace=[{"kind": "verify", "text": "All ingredients cleared the index."}],
        reveal_at=reveal_at,
        approval_status=approval_status,
    )
    session.add(row)
    await session.flush()
    return row


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(_EMAIL, token_version=1)}"}


# --- GET /api/v1/daily/meals ------------------------------------------------------


async def test_board_revealed_when_approved_and_past_reveal(
    client: AsyncClient, session: AsyncSession
) -> None:
    reveal_at = datetime.now(UTC) - timedelta(hours=2)
    await _add_suggestion(session, reveal_at=reveal_at)

    resp = await client.get("/api/v1/daily/meals")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "revealed"
    assert body["model"] == "fake/test"
    assert body["meals"][0]["name"] == "Courgette ribbon salad"
    assert body["meals"][0]["meal_type"] == "breakfast"
    assert body["trace"][0]["kind"] == "verify"


async def test_board_locked_before_reveal_time(client: AsyncClient, session: AsyncSession) -> None:
    reveal_at = datetime.now(UTC) + timedelta(hours=2)
    await _add_suggestion(session, reveal_at=reveal_at)

    resp = await client.get("/api/v1/daily/meals")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "locked"
    assert body["reveal_at"] is not None
    assert "meals" not in body


async def test_board_locked_when_no_board_scheduled(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/daily/meals")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "locked"
    assert body["reveal_at"] is None


async def test_public_card_omits_unverified_ingredients(
    client: AsyncClient, session: AsyncSession
) -> None:
    # Unverified ingredients are admin-review context, never shown on the public card.
    reveal_at = datetime.now(UTC) - timedelta(hours=2)
    await _add_suggestion(session, reveal_at=reveal_at, unverified=["mystery spice"])

    resp = await client.get("/api/v1/daily/meals")

    body = resp.json()
    assert body["status"] == "revealed"
    assert "unverified_ingredients" not in body["meals"][0]


async def test_board_read_sets_cache_control(client: AsyncClient, session: AsyncSession) -> None:
    reveal_at = datetime.now(UTC) - timedelta(hours=2)
    await _add_suggestion(session, reveal_at=reveal_at)

    resp = await client.get("/api/v1/daily/meals")

    assert resp.headers["cache-control"] == "public, max-age=120"


# --- admin gate -------------------------------------------------------------------


async def test_list_daily_without_a_token_is_401(client: AsyncClient) -> None:
    resp = await client.get("/admin/daily")
    assert resp.status_code == 401


# --- GET /admin/daily -------------------------------------------------------------


async def test_list_pending_daily_returns_review_detail(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)
    reveal_at = datetime.now(UTC) + timedelta(days=1)
    await _add_suggestion(
        session, reveal_at=reveal_at, approval_status=ApprovalStatus.PENDING, name="Pending salad"
    )
    await _add_suggestion(
        session,
        meal_type=MealType.LUNCH,
        reveal_at=reveal_at,
        approval_status=ApprovalStatus.APPROVED,
        name="Approved bake",
    )

    resp = await client.get("/admin/daily", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert [item["content"]["name"] for item in body] == ["Pending salad"]
    item = body[0]
    # The reviewer sees the full content and the trace, not just a title.
    assert item["content"]["ingredients"] == [{"name": "courgette", "category": "vegetable"}]
    assert item["reasoning_trace"][0]["kind"] == "verify"
    assert item["date"] == reveal_at.date().isoformat()


async def test_admin_queue_surfaces_unverified_ingredients(
    client: AsyncClient, session: AsyncSession
) -> None:
    # The reviewer must see what code could not verify, to close the gap the public
    # card hides (the safety invariant).
    await _add_admin(session)
    reveal_at = datetime.now(UTC) + timedelta(days=1)
    await _add_suggestion(
        session,
        reveal_at=reveal_at,
        approval_status=ApprovalStatus.PENDING,
        unverified=["mystery spice"],
    )

    resp = await client.get("/admin/daily", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json()[0]["content"]["unverified_ingredients"] == ["mystery spice"]


# --- PATCH /admin/daily/{id}/approve | reject -------------------------------------


async def test_approve_flips_status_and_stamps_the_actor(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)
    row = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) + timedelta(days=1),
        approval_status=ApprovalStatus.PENDING,
    )

    resp = await client.patch(f"/admin/daily/{row.id}/approve", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "approved"
    assert body["approved_by"] == _EMAIL
    assert body["approved_at"] is not None

    await session.flush()
    await session.refresh(row)
    assert row.approval_status is ApprovalStatus.APPROVED
    assert row.approved_by == _EMAIL


async def test_reject_flips_status_and_clears_stamp(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)
    row = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) + timedelta(days=1),
        approval_status=ApprovalStatus.APPROVED,
    )
    row.approved_by = "someone@example.com"
    await session.flush()

    resp = await client.patch(f"/admin/daily/{row.id}/reject", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "rejected"
    assert body["approved_by"] is None
    assert body["approved_at"] is None


async def test_approve_unknown_suggestion_is_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)

    resp = await client.patch(
        "/admin/daily/00000000-0000-0000-0000-000000000000/approve", headers=_auth_header()
    )

    assert resp.status_code == 404


# --- POST /admin/daily/generate (live SSE) ----------------------------------------


class _FakeStreamer:
    """Stands in for ComposerStreamer: a scripted trace then the composed meal.

    Yields the same discriminated envelopes the real ``ComposerAgent.stream``
    emits, so the route's type-switch is exercised honestly.
    """

    async def stream(self, meal_type: MealType) -> AsyncIterator[str]:
        yield TraceStreamItem(
            event=TraceEvent(kind="reject", text="Dropped parmesan — avoid.", ingredient="parmesan")
        ).model_dump_json()
        meal = ComposedMeal(
            name="Courgette ribbon salad",
            meal_type=meal_type,
            description="raw courgette ribbons with olive oil and fresh herbs",
            ingredients=[ProposedIngredient(name="courgette", category="vegetable")],
            recipe=["Peel into ribbons."],
            tags=["fresh"],
            reasoning_trace=[],
            model="fake/test",
        )
        yield MealStreamItem.of(meal).model_dump_json()


class _BoomStreamer:
    """Streams one step, then fails the way a mid-stream DB or serialization error would.

    The failure is neither ComposerExhausted nor LLMError, so it exercises the
    generator's catch-all backstop rather than the tailored branches.
    """

    async def stream(self, meal_type: MealType) -> AsyncIterator[str]:
        yield TraceStreamItem(
            event=TraceEvent(kind="check", text="Checking courgette.", ingredient="courgette")
        ).model_dump_json()
        raise RuntimeError("boom: the database connection dropped")


class _ExhaustedStreamer:
    """Streams a step, then raises ComposerExhausted as a non-converging run does.

    Low-histamine cooking is restrictive, so the loop can spend its whole budget
    without a safe submission; the route turns that into a friendly error event.
    """

    async def stream(self, meal_type: MealType) -> AsyncIterator[str]:
        yield TraceStreamItem(
            event=TraceEvent(kind="reject", text="Rejected the draft: too many flagged.")
        ).model_dump_json()
        raise ComposerExhausted("Composer exhausted its iterations.")


class _LLMErrorStreamer:
    """Streams a step, then fails with an LLMError as a mid-run model call can.

    Unlike the generic backstop, which hides the detail, the route's LLMError
    branch is a clean domain message and reaches the client verbatim.
    """

    async def stream(self, meal_type: MealType) -> AsyncIterator[str]:
        yield TraceStreamItem(
            event=TraceEvent(kind="check", text="Checking courgette.", ingredient="courgette")
        ).model_dump_json()
        raise LLMInvocationError("The model cannot call tools.")


@asynccontextmanager
async def _sse_client(
    session: AsyncSession, streamer_cls: type[object]
) -> AsyncIterator[AsyncClient]:
    """An admin client whose composer is the given scripted fake, sharing the session."""
    app = create_app()

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    app.dependency_overrides[get_composer_streamer] = streamer_cls
    limiter.enabled = False
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        limiter.enabled = True


@pytest_asyncio.fixture
async def sse_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """The default live-compose client: a scripted trace then a composed meal."""
    async with _sse_client(session, _FakeStreamer) as http_client:
        yield http_client


async def test_generate_without_a_token_is_401(client: AsyncClient) -> None:
    resp = await client.post("/admin/daily/generate", json={"meal_type": "breakfast"})
    assert resp.status_code == 401


async def test_generate_streams_trace_then_meal(
    sse_client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)

    resp = await sse_client.post(
        "/admin/daily/generate", json={"meal_type": "lunch"}, headers=_auth_header()
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "event: trace" in body
    assert "event: meal" in body
    assert "Dropped parmesan" in body
    assert "Courgette ribbon salad" in body


async def test_generate_with_a_bad_meal_type_is_422(
    sse_client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)

    resp = await sse_client.post(
        "/admin/daily/generate", json={"meal_type": "brunch"}, headers=_auth_header()
    )

    assert resp.status_code == 422


async def test_generate_unexpected_failure_closes_as_an_error_event(
    session: AsyncSession,
) -> None:
    await _add_admin(session)

    async with _sse_client(session, _BoomStreamer) as client:
        resp = await client.post(
            "/admin/daily/generate", json={"meal_type": "lunch"}, headers=_auth_header()
        )

    # The stream already opened with a 200, so the failure has to arrive as a terminal
    # error event; a truncated stream would read as success on the client.
    assert resp.status_code == 200
    body = resp.text
    assert "event: trace" in body
    assert "event: error" in body
    assert "Something went wrong" in body
    # The raw exception detail stays in the server log, never the response.
    assert "boom" not in body


async def test_generate_exhausted_closes_as_an_error_event(session: AsyncSession) -> None:
    await _add_admin(session)

    async with _sse_client(session, _ExhaustedStreamer) as client:
        resp = await client.post(
            "/admin/daily/generate", json={"meal_type": "lunch"}, headers=_auth_header()
        )

    assert resp.status_code == 200
    body = resp.text
    assert "event: trace" in body
    assert "event: error" in body
    assert "could not finish a safe meal" in body


async def test_generate_llm_error_surfaces_its_message(session: AsyncSession) -> None:
    await _add_admin(session)

    async with _sse_client(session, _LLMErrorStreamer) as client:
        resp = await client.post(
            "/admin/daily/generate", json={"meal_type": "lunch"}, headers=_auth_header()
        )

    assert resp.status_code == 200
    body = resp.text
    assert "event: error" in body
    # A model failure is already user-safe, so its message is shown rather than hidden.
    assert "The model cannot call tools." in body


async def test_generate_while_a_run_is_in_flight_is_409(
    sse_client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)

    # Hold the same lock the route checks, standing in for a live run already streaming.
    await _generation_lock.acquire()
    try:
        resp = await sse_client.post(
            "/admin/daily/generate", json={"meal_type": "lunch"}, headers=_auth_header()
        )
    finally:
        _generation_lock.release()

    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"]


async def test_generate_releases_the_lock_after_a_run(
    sse_client: AsyncClient, session: AsyncSession
) -> None:
    await _add_admin(session)

    resp = await sse_client.post(
        "/admin/daily/generate", json={"meal_type": "lunch"}, headers=_auth_header()
    )

    assert resp.status_code == 200
    assert not _generation_lock.locked()


# --- _cache_max_age (pure) --------------------------------------------------------


def test_cache_max_age_revealed_is_modest() -> None:
    board = RevealedBoard(date=date(2026, 6, 16), model="fake/test", meals=[], trace=[])

    assert _cache_max_age(board, datetime(2026, 6, 16, 11, tzinfo=UTC)) == 120


def test_cache_max_age_locked_counts_down_to_reveal() -> None:
    board = LockedBoard(date=date(2026, 6, 16), reveal_at=datetime(2026, 6, 16, 9, 1, tzinfo=UTC))

    # 60s to reveal: above the floor, below the cap.
    assert _cache_max_age(board, datetime(2026, 6, 16, 9, tzinfo=UTC)) == 60


def test_cache_max_age_locked_caps_a_far_future_reveal() -> None:
    board = LockedBoard(date=date(2026, 6, 16), reveal_at=datetime(2026, 6, 16, 10, tzinfo=UTC))

    assert _cache_max_age(board, datetime(2026, 6, 16, 0, tzinfo=UTC)) == 300


def test_cache_max_age_floors_past_reveal_and_unscheduled() -> None:
    now = datetime(2026, 6, 16, 11, tzinfo=UTC)
    past_reveal = LockedBoard(
        date=date(2026, 6, 16), reveal_at=datetime(2026, 6, 16, 10, tzinfo=UTC)
    )
    unscheduled = LockedBoard(date=date(2026, 6, 16), reveal_at=None)

    assert _cache_max_age(past_reveal, now) == 30
    assert _cache_max_age(unscheduled, now) == 30
