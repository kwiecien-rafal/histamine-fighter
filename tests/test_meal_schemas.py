"""Tests for the pure ingredient-list helpers shared by both route layers."""

from app.enums import AdaptationAction, CulinaryRole
from app.schemas.meal import Adaptation, ConfirmedIngredient, apply_adaptations


def _confirmed(name: str, category: str | None = None) -> ConfirmedIngredient:
    return ConfirmedIngredient(name=name, category=category)


def _adaptation(
    *names: str,
    action: AdaptationAction,
    swap: str | None = None,
    role: CulinaryRole = CulinaryRole.SUPPORTING,
) -> Adaptation:
    return Adaptation(
        ingredients=list(names),
        role=role,
        action=action,
        swap=swap,
        reason="Keeps the dish working.",
    )


def test_a_swap_takes_the_place_of_what_it_replaces() -> None:
    applied = apply_adaptations(
        [_confirmed("spaghetti"), _confirmed("parmesan"), _confirmed("basil")],
        [_adaptation("parmesan", action=AdaptationAction.SWAP, swap="young gouda")],
    )

    assert [item.name for item in applied] == ["spaghetti", "young gouda", "basil"]


def test_an_omitted_ingredient_leaves_the_list() -> None:
    applied = apply_adaptations(
        [_confirmed("spaghetti"), _confirmed("red wine")],
        [_adaptation("red wine", action=AdaptationAction.OMIT)],
    )

    assert [item.name for item in applied] == ["spaghetti"]


def test_an_ingredient_nothing_replaces_is_kept() -> None:
    applied = apply_adaptations(
        [_confirmed("spaghetti"), _confirmed("tomato", "vegetable")],
        [_adaptation("tomato", action=AdaptationAction.NO_SAFE_SWAP)],
    )

    assert [(item.name, item.category) for item in applied] == [
        ("spaghetti", None),
        ("tomato", "vegetable"),
    ]


def test_every_action_applies_in_one_pass() -> None:
    applied = apply_adaptations(
        [
            _confirmed("spaghetti"),
            _confirmed("tomato"),
            _confirmed("parmesan"),
            _confirmed("red wine"),
            _confirmed("basil"),
        ],
        [
            _adaptation("tomato", action=AdaptationAction.NO_SAFE_SWAP),
            _adaptation("parmesan", action=AdaptationAction.SWAP, swap="young gouda"),
            _adaptation("red wine", action=AdaptationAction.OMIT),
        ],
    )

    assert [item.name for item in applied] == ["spaghetti", "tomato", "young gouda", "basil"]


def test_one_swap_covering_several_ingredients_lands_once() -> None:
    applied = apply_adaptations(
        [_confirmed("Parmesan"), _confirmed("pecorino"), _confirmed("spaghetti")],
        [_adaptation("parmesan", "pecorino", action=AdaptationAction.SWAP, swap="young gouda")],
    )

    assert [item.name for item in applied] == ["young gouda", "spaghetti"]
