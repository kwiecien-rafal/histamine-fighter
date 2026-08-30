"""Tests for the lookup card's swap rows: which advice the card shows, and its role badge."""

from app.enums import (
    AdaptationAction,
    CulinaryRole,
    DishIntegrity,
    RewriteOutcome,
    SafetyLevel,
)
from app.schemas.meal import (
    Adaptation,
    AdaptedDish,
    DishAssessmentResponse,
    IngredientChange,
)
from app.schemas.usage import LLMUsage
from app.web.deps import swap_rows


def _assessed(*adaptations: Adaptation) -> DishAssessmentResponse:
    return DishAssessmentResponse(
        dish="spaghetti bolognese",
        verdict=SafetyLevel.AVOID,
        explanation="Tomato is recorded as incompatible.",
        adaptations=list(adaptations),
        advisories=[],
        integrity=DishIntegrity.ALTERED,
        ingredients=[],
        model="stub/model",
        usage=LLMUsage(),
    )


def _rewritten(outcome: RewriteOutcome, *changes: IngredientChange) -> AdaptedDish:
    return AdaptedDish(
        dish="spaghetti bolognese",
        name="spaghetti bolognese",
        outcome=outcome,
        explanation="Courgette carries the sauce.",
        changes=list(changes),
        verdict=SafetyLevel.SAFE,
        model="stub/model",
        usage=LLMUsage(),
    )


def _adaptation(
    *names: str,
    action: AdaptationAction,
    swap: str | None = None,
    role: CulinaryRole = CulinaryRole.CORE,
) -> Adaptation:
    return Adaptation(
        ingredients=list(names),
        role=role,
        action=action,
        swap=swap,
        reason="Keeps the dish working.",
    )


def test_a_rewrite_shows_its_own_diff_with_the_role_joined_back_on() -> None:
    rows = swap_rows(
        _assessed(_adaptation("Parmesan", action=AdaptationAction.SWAP, swap="young gouda")),
        _rewritten(
            RewriteOutcome.ADAPTED,
            IngredientChange(
                original="parmesan", replacement="young gouda", reason="Melts the same."
            ),
        ),
    )

    assert rows == [
        (["parmesan"], "young gouda", CulinaryRole.CORE, "Melts the same.", False),
    ]


def test_a_change_the_index_never_flagged_carries_no_role() -> None:
    """The model may rework an ingredient nothing was wrong with; a role would be invented."""
    rows = swap_rows(
        _assessed(),
        _rewritten(
            RewriteOutcome.ADAPTED,
            IngredientChange(original="basil", replacement=None, reason="Crowded the sauce."),
        ),
    )

    assert rows[0].role is None
    assert rows[0].replacement is None


def test_a_dish_with_no_version_advises_on_the_dish_as_named() -> None:
    """No new dish to diff, so the rows are what to do about the one that was asked about."""
    rows = swap_rows(
        _assessed(
            _adaptation("tomato", action=AdaptationAction.NO_SAFE_SWAP),
            _adaptation("red wine", action=AdaptationAction.OMIT, role=CulinaryRole.SUPPORTING),
        ),
        _rewritten(RewriteOutcome.IMPOSSIBLE),
    )

    # The one nothing replaces is the row that stays; the rest still read as advice.
    assert [(row.ingredients, row.kept) for row in rows] == [
        (["tomato"], True),
        (["red wine"], False),
    ]


def test_a_dish_nothing_was_wrong_with_has_no_advice_to_give() -> None:
    assert swap_rows(_assessed(), _rewritten(RewriteOutcome.UNCHANGED)) == []
