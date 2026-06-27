"""Tests for the daily board generation job: insert, skip, recompose, durability.

``build_board`` is driven with a fake compose callable (no LLM) and a
``checkpoint=session.flush`` so the work stays inside the rolled-back test
transaction instead of committing like the production path does.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composer import ComposerExhausted
from app.config import settings
from app.enums import ApprovalStatus, MealType
from app.llm.errors import LLMInvocationError
from app.models import DailySuggestion
from app.schemas.meal import ComposedMeal, ProposedIngredient, TraceEvent
from app.scripts.generate_daily_meals import (
    ComposeFn,
    _parse_args,
    _target_dates,
    build_board,
    build_boards,
)

_DAY = date(2026, 6, 20)


def _meal(meal_type: MealType, *, name: str | None = None) -> ComposedMeal:
    return ComposedMeal(
        name=name or f"{meal_type.value} dish",
        meal_type=meal_type,
        description="something safe and fresh",
        ingredients=[ProposedIngredient(name="buckwheat", category="grain")],
        recipe=["Cook it."],
        tags=["warm"],
        unverified_ingredients=[],
        model="fake/test",
        reasoning_trace=[TraceEvent(kind="verify", text=f"{meal_type.value} cleared")],
    )


def _compose_from(results: dict[MealType, ComposedMeal | Exception]) -> ComposeFn:
    """A fake composer: returns the mapped meal, or raises the mapped error."""

    async def compose(meal_type: MealType) -> ComposedMeal:
        outcome = results[meal_type]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return compose


async def _add_row(
    session: AsyncSession,
    *,
    meal_type: MealType,
    approval_status: ApprovalStatus,
    on: date = _DAY,
    name: str = "Existing",
) -> DailySuggestion:
    content: dict[str, Any] = {
        "name": name,
        "description": "",
        "ingredients": [],
        "recipe": None,
        "tags": [],
        "unverified_ingredients": [],
    }
    row = DailySuggestion(
        suggestion_date=on,
        meal_type=meal_type,
        content=content,
        model="old/model",
        reasoning_trace=[],
        reveal_at=datetime(on.year, on.month, on.day, 10, tzinfo=UTC),
        approval_status=approval_status,
    )
    session.add(row)
    await session.flush()
    return row


async def _rows(session: AsyncSession, on: date = _DAY) -> list[DailySuggestion]:
    stmt = select(DailySuggestion).where(DailySuggestion.suggestion_date == on)
    return list((await session.execute(stmt)).scalars().all())


async def _row(session: AsyncSession, meal_type: MealType) -> DailySuggestion:
    stmt = select(DailySuggestion).where(
        DailySuggestion.suggestion_date == _DAY,
        DailySuggestion.meal_type == meal_type,
    )
    return (await session.execute(stmt)).scalar_one()


# --- build_board ------------------------------------------------------------------


async def test_inserts_pending_rows_for_empty_slots(session: AsyncSession) -> None:
    compose = _compose_from({meal_type: _meal(meal_type) for meal_type in MealType})

    stored = await build_board(
        session, compose, _DAY, meal_types=list(MealType), checkpoint=session.flush
    )

    assert stored == 4
    rows = await _rows(session)
    assert {row.meal_type for row in rows} == set(MealType)
    assert all(row.approval_status is ApprovalStatus.PENDING for row in rows)


async def test_stored_content_keeps_unverified_ingredients(session: AsyncSession) -> None:
    meal = _meal(MealType.BREAKFAST).model_copy(
        update={"unverified_ingredients": ["mystery spice"]}
    )
    compose = _compose_from({MealType.BREAKFAST: meal})

    await build_board(
        session, compose, _DAY, meal_types=[MealType.BREAKFAST], checkpoint=session.flush
    )

    row = await _row(session, MealType.BREAKFAST)
    assert row.content["unverified_ingredients"] == ["mystery spice"]


async def test_skips_pending_and_approved_slots(session: AsyncSession) -> None:
    await _add_row(
        session, meal_type=MealType.BREAKFAST, approval_status=ApprovalStatus.PENDING, name="Keep"
    )
    await _add_row(
        session, meal_type=MealType.LUNCH, approval_status=ApprovalStatus.APPROVED, name="Live"
    )
    compose = _compose_from({meal_type: _meal(meal_type) for meal_type in MealType})

    stored = await build_board(
        session, compose, _DAY, meal_types=list(MealType), checkpoint=session.flush
    )

    assert stored == 2  # only dinner and snack were empty
    breakfast = await _row(session, MealType.BREAKFAST)
    assert breakfast.content["name"] == "Keep"
    assert breakfast.approval_status is ApprovalStatus.PENDING
    lunch = await _row(session, MealType.LUNCH)
    assert lunch.content["name"] == "Live"
    assert lunch.approval_status is ApprovalStatus.APPROVED


async def test_recomposes_a_rejected_slot_in_place(session: AsyncSession) -> None:
    rejected = await _add_row(
        session,
        meal_type=MealType.DINNER,
        approval_status=ApprovalStatus.REJECTED,
        name="Old rejected",
    )
    rejected.approved_by = "admin@example.com"
    rejected.approved_at = datetime(2026, 6, 20, 9, tzinfo=UTC)
    await session.flush()
    original_id = rejected.id

    compose = _compose_from({MealType.DINNER: _meal(MealType.DINNER, name="Fresh dinner")})
    stored = await build_board(
        session, compose, _DAY, meal_types=[MealType.DINNER], checkpoint=session.flush
    )

    assert stored == 1
    rows = await _rows(session)
    assert len(rows) == 1  # recomposed in place, not duplicated
    row = rows[0]
    assert row.id == original_id
    assert row.content["name"] == "Fresh dinner"
    assert row.approval_status is ApprovalStatus.PENDING
    assert row.approved_by is None
    assert row.approved_at is None


async def test_skips_slots_the_composer_cannot_finish(session: AsyncSession) -> None:
    compose = _compose_from(
        {
            MealType.BREAKFAST: _meal(MealType.BREAKFAST),
            MealType.LUNCH: ComposerExhausted("no convergence"),
            MealType.DINNER: LLMInvocationError("model cannot call tools"),
            MealType.SNACK: _meal(MealType.SNACK),
        }
    )

    stored = await build_board(
        session, compose, _DAY, meal_types=list(MealType), checkpoint=session.flush
    )

    assert stored == 2
    rows = await _rows(session)
    assert {row.meal_type for row in rows} == {MealType.BREAKFAST, MealType.SNACK}


async def test_is_durable_when_a_later_meal_errors_unexpectedly(session: AsyncSession) -> None:
    compose = _compose_from(
        {
            MealType.BREAKFAST: _meal(MealType.BREAKFAST),
            MealType.LUNCH: _meal(MealType.LUNCH),
            MealType.DINNER: RuntimeError("unexpected"),
            MealType.SNACK: _meal(MealType.SNACK),
        }
    )

    with pytest.raises(RuntimeError):
        await build_board(
            session, compose, _DAY, meal_types=list(MealType), checkpoint=session.flush
        )

    rows = await _rows(session)
    # The two meals before the failure were checkpointed; dinner and snack never ran.
    assert {row.meal_type for row in rows} == {MealType.BREAKFAST, MealType.LUNCH}


async def test_only_composes_requested_meal_types(session: AsyncSession) -> None:
    compose = _compose_from({MealType.SNACK: _meal(MealType.SNACK)})

    stored = await build_board(
        session, compose, _DAY, meal_types=[MealType.SNACK], checkpoint=session.flush
    )

    assert stored == 1
    rows = await _rows(session)
    assert [row.meal_type for row in rows] == [MealType.SNACK]


# --- build_boards (horizon gap-filler) --------------------------------------------

_D1 = date(2026, 6, 21)
_D2 = date(2026, 6, 22)
_D3 = date(2026, 6, 23)


def _full_compose() -> ComposeFn:
    return _compose_from({meal_type: _meal(meal_type) for meal_type in MealType})


async def test_fills_multiple_empty_days(session: AsyncSession) -> None:
    stored = await build_boards(
        session, _full_compose(), [_D1, _D2], meal_types=list(MealType), checkpoint=session.flush
    )

    assert stored == 8
    assert len(await _rows(session, _D1)) == 4
    assert len(await _rows(session, _D2)) == 4


async def test_skips_a_covered_day(session: AsyncSession) -> None:
    for meal_type in MealType:
        await _add_row(
            session, on=_D1, meal_type=meal_type, approval_status=ApprovalStatus.APPROVED
        )

    stored = await build_boards(
        session, _full_compose(), [_D1, _D2], meal_types=list(MealType), checkpoint=session.flush
    )

    assert stored == 4  # only the empty second day
    assert all(row.content["name"] == "Existing" for row in await _rows(session, _D1))
    assert len(await _rows(session, _D2)) == 4


async def test_completes_a_partial_day(session: AsyncSession) -> None:
    await _add_row(
        session, on=_D1, meal_type=MealType.BREAKFAST, approval_status=ApprovalStatus.PENDING
    )

    stored = await build_boards(
        session, _full_compose(), [_D1], meal_types=list(MealType), checkpoint=session.flush
    )

    assert stored == 3  # breakfast was already present
    assert len(await _rows(session, _D1)) == 4


async def test_backfills_a_gap_between_covered_days(session: AsyncSession) -> None:
    for day in (_D1, _D3):
        for meal_type in MealType:
            await _add_row(
                session, on=day, meal_type=meal_type, approval_status=ApprovalStatus.APPROVED
            )

    stored = await build_boards(
        session,
        _full_compose(),
        [_D1, _D2, _D3],
        meal_types=list(MealType),
        checkpoint=session.flush,
    )

    assert stored == 4  # only the empty middle day
    assert len(await _rows(session, _D2)) == 4
    assert all(row.content["name"] == "Existing" for row in await _rows(session, _D1))
    assert all(row.content["name"] == "Existing" for row in await _rows(session, _D3))


# --- CLI args and target dates ----------------------------------------------------


def test_parse_args_defaults_to_no_date_and_all_meals() -> None:
    args = _parse_args([])

    assert args.date is None
    assert args.horizon is None
    assert args.meal_type is None


def test_parse_args_reads_date_and_meal_type() -> None:
    args = _parse_args(["--date", "2026-06-20", "--meal-type", "dinner"])

    assert args.date == date(2026, 6, 20)
    assert args.meal_type == "dinner"


def test_target_dates_forces_a_single_date_when_given() -> None:
    assert _target_dates(_parse_args(["--date", "2026-06-20"])) == [date(2026, 6, 20)]


def test_target_dates_defaults_to_the_configured_horizon_from_tomorrow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "daily_cron_horizon_days", 3)
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)

    dates = _target_dates(_parse_args([]))

    assert dates == [tomorrow + timedelta(days=offset) for offset in range(3)]


def test_target_dates_horizon_flag_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "daily_cron_horizon_days", 1)
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)

    dates = _target_dates(_parse_args(["--horizon", "2"]))

    assert dates == [tomorrow, tomorrow + timedelta(days=1)]
