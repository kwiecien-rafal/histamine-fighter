"""Tests for the daily board service: locked/revealed decisions and review.

These run against the test database (the conftest ``session`` fixture). The
locked/revealed logic takes an explicit ``now``, so the clock is deterministic
without patching.
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ApprovalStatus, MealType
from app.models import DailySuggestion
from app.services.daily_service import DailyService

_DAY = date(2026, 6, 16)
_REVEAL = datetime(2026, 6, 16, 10, tzinfo=UTC)
_BEFORE = datetime(2026, 6, 16, 9, tzinfo=UTC)
_AFTER = datetime(2026, 6, 16, 11, tzinfo=UTC)


def _content(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": "raw courgette ribbons with olive oil and fresh herbs",
        "ingredients": [{"name": "courgette", "category": "vegetable"}],
        "recipe": ["Peel into ribbons.", "Toss with oil and herbs."],
        "tags": ["fresh"],
    }


async def _add(
    session: AsyncSession,
    *,
    meal_type: MealType,
    on: date = _DAY,
    reveal_at: datetime = _REVEAL,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
    name: str | None = None,
    trace: list[dict[str, Any]] | None = None,
    model: str = "fake/test",
) -> DailySuggestion:
    row = DailySuggestion(
        suggestion_date=on,
        meal_type=meal_type,
        content=_content(name or f"{meal_type.value} salad"),
        model=model,
        reasoning_trace=trace or [{"kind": "verify", "text": f"{meal_type.value} cleared."}],
        reveal_at=reveal_at,
        approval_status=approval_status,
    )
    session.add(row)
    await session.flush()
    return row


# --- board_for: locked vs. revealed ----------------------------------------------


async def test_board_revealed_past_reveal_and_approved(session: AsyncSession) -> None:
    await _add(session, meal_type=MealType.BREAKFAST)
    await _add(session, meal_type=MealType.LUNCH)

    board = await DailyService(session).board_for(_DAY, now=_AFTER)

    assert board.status == "revealed"
    assert board.date == _DAY
    assert board.model == "fake/test"
    assert [card.meal_type for card in board.meals] == [MealType.BREAKFAST, MealType.LUNCH]


async def test_board_locked_before_reveal_time(session: AsyncSession) -> None:
    await _add(session, meal_type=MealType.BREAKFAST)

    board = await DailyService(session).board_for(_DAY, now=_BEFORE)

    assert board.status == "locked"
    assert board.reveal_at == _REVEAL


async def test_board_locked_when_nothing_approved(session: AsyncSession) -> None:
    await _add(session, meal_type=MealType.BREAKFAST, approval_status=ApprovalStatus.PENDING)

    board = await DailyService(session).board_for(_DAY, now=_AFTER)

    assert board.status == "locked"
    assert board.reveal_at == _REVEAL


async def test_board_with_no_rows_is_locked_with_no_reveal_at(session: AsyncSession) -> None:
    board = await DailyService(session).board_for(_DAY, now=_AFTER)

    assert board.status == "locked"
    assert board.reveal_at is None


async def test_revealed_board_drops_unapproved_meals(session: AsyncSession) -> None:
    await _add(session, meal_type=MealType.BREAKFAST, approval_status=ApprovalStatus.APPROVED)
    await _add(session, meal_type=MealType.LUNCH, approval_status=ApprovalStatus.REJECTED)
    await _add(session, meal_type=MealType.DINNER, approval_status=ApprovalStatus.PENDING)

    board = await DailyService(session).board_for(_DAY, now=_AFTER)

    assert board.status == "revealed"
    assert [card.meal_type for card in board.meals] == [MealType.BREAKFAST]


async def test_cards_and_trace_follow_meal_order(session: AsyncSession) -> None:
    # Inserted out of order; the board sorts into natural meal order, not by id.
    await _add(session, meal_type=MealType.DINNER, trace=[{"kind": "verify", "text": "d"}])
    await _add(session, meal_type=MealType.BREAKFAST, trace=[{"kind": "verify", "text": "b"}])

    board = await DailyService(session).board_for(_DAY, now=_AFTER)

    assert board.status == "revealed"
    assert [card.meal_type for card in board.meals] == [MealType.BREAKFAST, MealType.DINNER]
    assert [event.text for event in board.trace] == ["b", "d"]


async def test_revealed_board_drops_model_draft_events(session: AsyncSession) -> None:
    # `draft` is the model's own prose; the public board shows only code-authored steps.
    await _add(
        session,
        meal_type=MealType.BREAKFAST,
        trace=[
            {"kind": "draft", "text": "thinking out loud about breakfast"},
            {"kind": "verify", "text": "cleared the index"},
        ],
    )

    board = await DailyService(session).board_for(_DAY, now=_AFTER)

    assert board.status == "revealed"
    assert [event.kind for event in board.trace] == ["verify"]


async def test_board_ignores_other_dates(session: AsyncSession) -> None:
    await _add(session, meal_type=MealType.BREAKFAST, on=date(2026, 6, 15))

    board = await DailyService(session).board_for(_DAY, now=_AFTER)

    assert board.status == "locked"
    assert board.reveal_at is None


# --- review: list, approve, reject ------------------------------------------------


async def test_list_for_review_filters_by_status(session: AsyncSession) -> None:
    await _add(session, meal_type=MealType.BREAKFAST, approval_status=ApprovalStatus.PENDING)
    await _add(session, meal_type=MealType.LUNCH, approval_status=ApprovalStatus.APPROVED)

    pending = await DailyService(session).list_for_review(ApprovalStatus.PENDING)

    assert [row.meal_type for row in pending] == [MealType.BREAKFAST]


async def test_approve_stamps_actor_and_time(session: AsyncSession) -> None:
    row = await _add(session, meal_type=MealType.BREAKFAST, approval_status=ApprovalStatus.PENDING)

    updated = await DailyService(session).approve(row.id, actor="admin@example.com")

    assert updated is not None
    assert updated.approval_status is ApprovalStatus.APPROVED
    assert updated.approved_by == "admin@example.com"
    assert updated.approved_at is not None


async def test_reject_clears_any_approval_stamp(session: AsyncSession) -> None:
    row = await _add(session, meal_type=MealType.BREAKFAST, approval_status=ApprovalStatus.APPROVED)
    row.approved_by = "admin@example.com"
    row.approved_at = _REVEAL
    await session.flush()

    updated = await DailyService(session).reject(row.id)

    assert updated is not None
    assert updated.approval_status is ApprovalStatus.REJECTED
    assert updated.approved_by is None
    assert updated.approved_at is None


async def test_approve_unknown_suggestion_returns_none(session: AsyncSession) -> None:
    from uuid import UUID

    result = await DailyService(session).approve(
        UUID("00000000-0000-0000-0000-000000000000"), actor="admin@example.com"
    )

    assert result is None
