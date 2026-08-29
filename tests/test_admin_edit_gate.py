"""Unit tests for the admin gate policy in ``ensure_safe``.

The endpoint tests cover the happy paths against real index rows; the policy edges
(depends never blocks, confirmation clears avoid but never unverifiable) are pure
rules over a ``MealVerification``, so they are pinned here without a database.
"""

import pytest
from fastapi import HTTPException

from app.agents.meal_verification import MealVerification
from app.api.admin.edits import ensure_safe
from app.enums import TraceReading
from app.schemas.meal import CautionedIngredient


def _verification(blockers: list[tuple[str, TraceReading]]) -> MealVerification:
    return MealVerification(blockers=blockers, cautioned=[], unverified=[], recipe_flags=[])


def test_no_blockers_passes_and_records_nothing() -> None:
    assert ensure_safe(_verification([]), confirmed=False) == []


def test_cautioned_never_blocks() -> None:
    # A depends reading lands in cautioned now, never in blockers, so the gate
    # passes it through untouched for the caller to record.
    verification = MealVerification(
        blockers=[],
        cautioned=[CautionedIngredient(name="spinach", note="use in moderation")],
        unverified=[],
        recipe_flags=[],
    )

    assert ensure_safe(verification, confirmed=False) == []


def test_avoid_blocks_with_a_confirmable_422() -> None:
    verification = _verification([("parmesan", TraceReading.AVOID)])

    with pytest.raises(HTTPException) as excinfo:
        ensure_safe(verification, confirmed=False)

    detail = excinfo.value.detail
    assert isinstance(detail, dict)
    assert detail["blockers"] == ["parmesan (avoid)"]
    assert detail["can_confirm"] is True


def test_confirmation_clears_avoid_and_returns_it_for_recording() -> None:
    verification = _verification([("parmesan", TraceReading.AVOID)])

    assert ensure_safe(verification, confirmed=True) == ["parmesan (avoid)"]


def test_unverifiable_blocks_even_when_confirmed() -> None:
    # Confirming a reading the index could not produce would be confirming blind.
    verification = _verification(
        [("mystery", TraceReading.UNVERIFIABLE), ("parmesan", TraceReading.AVOID)]
    )

    with pytest.raises(HTTPException) as excinfo:
        ensure_safe(verification, confirmed=True)

    detail = excinfo.value.detail
    assert isinstance(detail, dict)
    assert detail["blockers"] == ["mystery (unverifiable)"]
    assert detail["can_confirm"] is False
