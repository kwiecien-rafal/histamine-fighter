"""Endpoint tests for the daily board: public read and admin review.

These run against the test database (the conftest ``client``/``authenticated_client``
share the rolled-back session). The public read derives "today" from the real UTC
clock, so rows are created for today with a reveal time deliberately past or future.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.daily import _cache_max_age
from app.enums import ApprovalStatus, Compatibility, MealType
from app.models import DailySuggestion, HistamineIngredient
from app.models.user import User
from app.schemas.daily import LockedBoard, RevealedBoard


async def _seed_index(session: AsyncSession) -> None:
    session.add_all(
        [
            HistamineIngredient(
                name="parmesan",
                sources=["test"],
                compatibility=Compatibility.INCOMPATIBLE,
                category="aged hard cheese",
            ),
            HistamineIngredient(
                name="courgette",
                sources=["test"],
                compatibility=Compatibility.WELL_TOLERATED,
                category="vegetable",
            ),
        ]
    )
    await session.flush()


def _edit_body(suggestion: DailySuggestion, *, ingredients: list[dict[str, str]]) -> dict[str, Any]:
    content = suggestion.content
    return {
        "name": content["name"],
        "description": content["description"],
        "ingredients": ingredients,
        "recipe": content["recipe"],
        "tags": content["tags"],
    }


def _content(name: str, *, unverified: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": "raw courgette ribbons with olive oil and fresh herbs",
        "ingredients": [{"name": "courgette", "category": "vegetable"}],
        "recipe": ["Peel into ribbons.", "Toss with oil and herbs."],
        "tags": ["fresh"],
        "unverified_ingredients": unverified or [],
    }


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
    # Model and trace ride on each card now, for per-card attribution and the
    # "watch how it was composed" replay.
    assert body["meals"][0]["model"] == "fake/test"
    assert body["meals"][0]["trace"][0]["kind"] == "verify"


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


# --- GET /api/v1/daily/meals/{date} -----------------------------------------------


async def test_dated_board_returns_a_past_revealed_day(
    client: AsyncClient, session: AsyncSession
) -> None:
    reveal_at = datetime.now(UTC) - timedelta(days=3)
    await _add_suggestion(session, reveal_at=reveal_at)
    on = reveal_at.date()

    resp = await client.get(f"/api/v1/daily/meals/{on.isoformat()}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "revealed"
    assert body["date"] == on.isoformat()
    assert body["meals"][0]["name"] == "Courgette ribbon salad"
    # A past day is immutable, so it caches for an hour rather than tracking the clock.
    assert resp.headers["cache-control"] == "public, max-age=3600"


async def test_dated_board_past_day_with_nothing_approved_is_locked(
    client: AsyncClient, session: AsyncSession
) -> None:
    reveal_at = datetime.now(UTC) - timedelta(days=3)
    await _add_suggestion(session, reveal_at=reveal_at, approval_status=ApprovalStatus.PENDING)
    on = reveal_at.date()

    resp = await client.get(f"/api/v1/daily/meals/{on.isoformat()}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "locked"
    assert "meals" not in body


async def test_dated_board_accepts_today_with_the_live_cache(
    client: AsyncClient, session: AsyncSession
) -> None:
    reveal_at = datetime.now(UTC) - timedelta(hours=2)
    await _add_suggestion(session, reveal_at=reveal_at)
    today = datetime.now(UTC).date()

    resp = await client.get(f"/api/v1/daily/meals/{today.isoformat()}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "revealed"
    # Today through the dated route still tracks the reveal clock, not the past-day cap.
    assert resp.headers["cache-control"] == "public, max-age=120"


async def test_dated_board_older_than_the_window_is_404(client: AsyncClient) -> None:
    old = (datetime.now(UTC) - timedelta(days=8)).date()

    resp = await client.get(f"/api/v1/daily/meals/{old.isoformat()}")

    assert resp.status_code == 404


async def test_dated_board_future_date_is_404(client: AsyncClient) -> None:
    future = (datetime.now(UTC) + timedelta(days=1)).date()

    resp = await client.get(f"/api/v1/daily/meals/{future.isoformat()}")

    assert resp.status_code == 404


# --- GET /admin/daily/queue -------------------------------------------------------


async def test_admin_queue_slot_carries_review_detail(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    # The reviewer sees the full content, the trace, and what code could not verify, to
    # close the gap the public card hides (the safety invariant).
    reveal_at = datetime.now(UTC) + timedelta(days=1)
    await _add_suggestion(
        session,
        reveal_at=reveal_at,
        approval_status=ApprovalStatus.PENDING,
        unverified=["mystery spice"],
    )

    resp = await authenticated_client.get("/admin/daily/queue")

    assert resp.status_code == 200
    slot = resp.json()[0]["slots"][0]
    assert slot["content"]["ingredients"] == [{"name": "courgette", "category": "vegetable"}]
    assert slot["reasoning_trace"][0]["kind"] == "verify"
    assert slot["content"]["unverified_ingredients"] == ["mystery spice"]


# --- PATCH /admin/daily/{id}/approve | reject -------------------------------------


async def test_approve_flips_status_and_stamps_the_actor(
    authenticated_client: AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    row = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) + timedelta(days=1),
        approval_status=ApprovalStatus.PENDING,
    )

    resp = await authenticated_client.patch(f"/admin/daily/{row.id}/approve")

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "approved"
    assert body["approved_by"] == admin_user.email
    assert body["approved_at"] is not None

    await session.flush()
    await session.refresh(row)
    assert row.approval_status is ApprovalStatus.APPROVED
    assert row.approved_by == admin_user.email


async def test_reject_flips_status_and_clears_stamp(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    row = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) + timedelta(days=1),
        approval_status=ApprovalStatus.APPROVED,
    )
    row.approved_by = "someone@example.com"
    await session.flush()

    resp = await authenticated_client.patch(f"/admin/daily/{row.id}/reject")

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "rejected"
    assert body["approved_by"] is None
    assert body["approved_at"] is None


async def test_approve_unknown_suggestion_is_404(authenticated_client: AsyncClient) -> None:
    resp = await authenticated_client.patch(
        "/admin/daily/00000000-0000-0000-0000-000000000000/approve"
    )

    assert resp.status_code == 404


# --- DELETE /admin/daily/{id} -----------------------------------------------------


async def test_delete_suggestion_removes_the_row(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    row = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) + timedelta(days=1),
        approval_status=ApprovalStatus.PENDING,
    )

    resp = await authenticated_client.delete(f"/admin/daily/{row.id}")

    assert resp.status_code == 204
    await session.flush()
    remaining = (
        await session.execute(select(DailySuggestion).where(DailySuggestion.id == row.id))
    ).scalar_one_or_none()
    assert remaining is None


async def test_delete_unknown_suggestion_is_404(authenticated_client: AsyncClient) -> None:
    resp = await authenticated_client.delete("/admin/daily/00000000-0000-0000-0000-000000000000")

    assert resp.status_code == 404


# --- PATCH /admin/daily/{id} (edit) -----------------------------------------------


async def test_edit_pending_suggestion_rewrites_content_and_rederives_unverified(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    suggestion = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) + timedelta(days=1),
        approval_status=ApprovalStatus.PENDING,
    )
    body = _edit_body(
        suggestion,
        ingredients=[
            {"name": "courgette", "category": "vegetable"},
            {"name": "mystery spice", "category": "spice"},
        ],
    )

    resp = await authenticated_client.patch(f"/admin/daily/{suggestion.id}", json=body)

    assert resp.status_code == 200
    content = resp.json()["content"]
    assert [item["name"] for item in content["ingredients"]] == ["courgette", "mystery spice"]
    assert content["unverified_ingredients"] == ["mystery spice"]


async def test_edit_suggestion_rejects_an_introduced_blocker(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    suggestion = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) + timedelta(days=1),
        approval_status=ApprovalStatus.PENDING,
    )
    body = _edit_body(
        suggestion,
        ingredients=[
            {"name": "courgette", "category": "vegetable"},
            {"name": "parmesan", "category": "aged hard cheese"},
        ],
    )

    resp = await authenticated_client.patch(f"/admin/daily/{suggestion.id}", json=body)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("parmesan" in blocker for blocker in detail["blockers"])
    assert detail["can_confirm"] is True


async def test_edit_suggestion_confirm_flagged_saves_and_records(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    suggestion = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) + timedelta(days=1),
        approval_status=ApprovalStatus.PENDING,
    )
    body = _edit_body(
        suggestion,
        ingredients=[
            {"name": "courgette", "category": "vegetable"},
            {"name": "parmesan", "category": "aged hard cheese"},
        ],
    )
    body["confirm_flagged"] = True

    resp = await authenticated_client.patch(f"/admin/daily/{suggestion.id}", json=body)

    assert resp.status_code == 200
    assert resp.json()["content"]["unverified_ingredients"] == ["parmesan (avoid)"]


async def test_edit_an_approved_suggestion_is_409(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    suggestion = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) + timedelta(days=1),
        approval_status=ApprovalStatus.APPROVED,
    )
    body = _edit_body(suggestion, ingredients=[{"name": "courgette", "category": "vegetable"}])

    resp = await authenticated_client.patch(f"/admin/daily/{suggestion.id}", json=body)

    assert resp.status_code == 409


async def test_edit_suggestion_without_a_session_is_401(
    client: AsyncClient, session: AsyncSession
) -> None:
    suggestion = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) + timedelta(days=1),
        approval_status=ApprovalStatus.PENDING,
    )
    body = _edit_body(suggestion, ingredients=[{"name": "courgette", "category": "vegetable"}])

    resp = await client.patch(f"/admin/daily/{suggestion.id}", json=body)

    assert resp.status_code == 401


# --- _cache_max_age (pure) --------------------------------------------------------


def test_cache_max_age_revealed_is_modest() -> None:
    board = RevealedBoard(date=date(2026, 6, 16), model="fake/test", meals=[])

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
