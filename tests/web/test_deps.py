"""Tests for the lookup card's view models: its swap advice and its ingredient marks."""

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
    Advisory,
    DishAssessmentResponse,
    IngredientAssessment,
    IngredientChange,
    ProposedIngredient,
)
from app.schemas.usage import LLMUsage
from app.web.deps import dish_chips, swap_rows


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


def _readings(*rows: IngredientAssessment) -> list[IngredientAssessment]:
    return list(rows)


def _chips(
    names: list[str],
    assessment: DishAssessmentResponse,
    adapted: AdaptedDish,
    advisories: list[Advisory] | None = None,
) -> dict[str, str]:
    """The card's marks, keyed by ingredient, for the dish those inputs describe."""
    listed = [ProposedIngredient(name=name) for name in names]
    return {
        chip.name: chip.reading
        for chip in dish_chips(listed, advisories or [], assessment, adapted)
    }


def test_a_rewrite_carries_its_own_readings() -> None:
    """A cleared version is graded on its own list, so the assessment's marks do not apply."""
    adapted = _rewritten(RewriteOutcome.ADAPTED)
    adapted = adapted.model_copy(update={"unverified_ingredients": ["samphire"]})
    assessed = _assessed(_adaptation("tomato", action=AdaptationAction.NO_SAFE_SWAP)).model_copy(
        update={"ingredients": _readings()}
    )

    marks = _chips(
        ["courgette", "samphire", "basil"],
        assessed,
        adapted,
        [Advisory(ingredient="basil", note="Fresh only.")],
    )

    assert marks == {"courgette": "clear", "samphire": "unrated", "basil": "watch"}


def test_a_dish_with_no_version_marks_what_it_could_not_fix() -> None:
    """The compromise is on the chip: an ingredient nothing replaces is not left plain."""
    assessed = _assessed(_adaptation("tomato", action=AdaptationAction.NO_SAFE_SWAP)).model_copy(
        update={
            "ingredients": _readings(
                IngredientAssessment(name="tomato", safety=SafetyLevel.AVOID, found=True),
                IngredientAssessment(name="samphire", safety=SafetyLevel.SAFE, found=False),
                IngredientAssessment(
                    name="kimchi", safety=SafetyLevel.DEPENDS, found=False, error=True
                ),
            )
        }
    )

    marks = _chips(
        ["tomato", "samphire", "kimchi"], assessed, _rewritten(RewriteOutcome.IMPOSSIBLE)
    )

    # A lookup that failed keeps its caution rather than reading as nothing to note.
    assert marks == {"tomato": "kept", "samphire": "unrated", "kimchi": "unreadable"}
