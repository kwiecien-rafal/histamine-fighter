"""Unit tests for the structural quality gate (pure, no model, no database)."""

from app.agents.meal_quality import check_structure
from app.enums import MealType
from app.schemas.meal import ProposedIngredient


def _items(*pairs: tuple[str, str | None]) -> list[ProposedIngredient]:
    return [ProposedIngredient(name=name, category=category) for name, category in pairs]


_FULL_DINNER = _items(
    ("chicken breast", "fresh meat"),
    ("courgette", "vegetable"),
    ("olive oil", "fat"),
    ("quinoa", "grain"),
    ("basil", "herb"),
)
_STEPS = ["Prep.", "Cook.", "Serve."]


def test_a_full_dinner_passes() -> None:
    assert check_structure(MealType.DINNER, _FULL_DINNER, _STEPS) == []


def test_too_few_ingredients_is_reported() -> None:
    reasons = check_structure(MealType.DINNER, _FULL_DINNER[:3], _STEPS)

    assert any("at least 5 ingredients" in reason for reason in reasons)


def test_too_few_recipe_steps_is_reported() -> None:
    reasons = check_structure(MealType.DINNER, _FULL_DINNER, ["Cook everything."])

    assert any("at least 3 steps" in reason for reason in reasons)


def test_snack_has_lower_floors() -> None:
    snack = _items(("apple", "fruit"), ("almond butter", "nut"), ("oat cakes", "grain"))

    assert check_structure(MealType.SNACK, snack, ["Slice.", "Spread."]) == []


def test_dinner_without_a_protein_is_reported() -> None:
    vegetarian_sides = _items(
        ("courgette", "vegetable"),
        ("carrot", "vegetable"),
        ("olive oil", "fat"),
        ("rice", "grain"),
        ("basil", "herb"),
    )

    reasons = check_structure(MealType.DINNER, vegetarian_sides, _STEPS)

    assert any("protein" in reason for reason in reasons)


def test_dinner_without_produce_is_reported() -> None:
    beige = _items(
        ("chicken breast", "fresh meat"),
        ("rice", "grain"),
        ("olive oil", "fat"),
        ("oat flour", "grain"),
        ("salt", "seasoning"),
    )

    reasons = check_structure(MealType.DINNER, beige, _STEPS)

    assert any("vegetable or fruit" in reason for reason in reasons)


def test_plural_and_phrase_categories_still_match() -> None:
    plural = _items(
        ("chicken breast", "fresh meats"),
        ("courgette", "seasonal vegetables"),
        ("olive oil", "fat"),
        ("rice", "grain"),
        ("basil", "herb"),
    )

    assert check_structure(MealType.DINNER, plural, _STEPS) == []


def test_coverage_is_skipped_when_most_categories_are_missing() -> None:
    uncategorized = _items(
        ("courgette", None),
        ("carrot", None),
        ("olive oil", None),
        ("rice", "grain"),
        ("basil", None),
    )

    assert check_structure(MealType.DINNER, uncategorized, _STEPS) == []


def test_breakfast_skips_coverage_entirely() -> None:
    breakfast = _items(
        ("oats", "grain"),
        ("oat milk", "beverage"),
        ("maple syrup", "sweetener"),
        ("cinnamon", "spice"),
    )

    assert check_structure(MealType.BREAKFAST, breakfast, _STEPS) == []
