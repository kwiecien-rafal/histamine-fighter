"""The structural quality gate for a composed meal.

Pure functions over a submission, so the gate is unit-testable without a model or
a database. It exists to push back against stripping: the safety gate's feedback
only ever costs ingredients, so without a counter-gradient the loop's equilibrium
is a technically-safe but bare dish. Every reason returned here reads as "enrich
this", never "remove that".

The thresholds are editorial quality, not safety posture: they are tuned by
editing the constants alongside their tests, not per deployment (the moderate cap
is a setting because it changes what a fork accepts as risk; these do not).
"""

import re
from collections.abc import Sequence

from app.enums import MealType
from app.schemas.meal import ProposedIngredient

_MIN_INGREDIENTS: dict[MealType, int] = {
    MealType.BREAKFAST: 4,
    MealType.LUNCH: 5,
    MealType.DINNER: 5,
    MealType.SNACK: 3,
}

_MIN_RECIPE_STEPS: dict[MealType, int] = {
    MealType.BREAKFAST: 3,
    MealType.LUNCH: 3,
    MealType.DINNER: 3,
    MealType.SNACK: 2,
}

# Category coverage is asked of the main meals only: a breakfast or a snack
# without a protein is a legitimate dish, a dinner without one is a garnish.
_COVERAGE_MEAL_TYPES = frozenset({MealType.LUNCH, MealType.DINNER})

_PROTEIN_TOKENS = frozenset(
    {"meat", "poultry", "fish", "seafood", "egg", "dairy", "legume", "nut", "seed"}
)
_PRODUCE_TOKENS = frozenset({"vegetable", "fruit", "mushroom", "herb"})


def check_structure(
    meal_type: MealType,
    ingredients: Sequence[ProposedIngredient],
    recipe: Sequence[str] | None,
) -> list[str]:
    """Reasons a submission is too thin to accept; empty means it passes.

    Counts and steps are hard floors. Category coverage is judged only when the
    model categorized at least half the list: categories are model-supplied and
    optional, so their absence means "cannot judge", and blocking on it would set
    a trap the model cannot see its way out of.
    """
    reasons: list[str] = []
    minimum = _MIN_INGREDIENTS[meal_type]
    if len(ingredients) < minimum:
        reasons.append(
            f"a {meal_type.value} needs at least {minimum} ingredients, this has {len(ingredients)}"
        )

    steps = list(recipe or [])
    min_steps = _MIN_RECIPE_STEPS[meal_type]
    if len(steps) < min_steps:
        reasons.append(f"the recipe needs at least {min_steps} steps, this has {len(steps)}")

    categorized = [item.category for item in ingredients if item.category]
    if meal_type in _COVERAGE_MEAL_TYPES and len(categorized) * 2 >= len(ingredients):
        tokens = set[str]()
        for category in categorized:
            tokens.update(_category_tokens(category))
        if not tokens & _PROTEIN_TOKENS:
            reasons.append("it needs a protein (fish, poultry, egg, or a legume, for example)")
        if not tokens & _PRODUCE_TOKENS:
            reasons.append("it needs a vegetable or fruit component")
    return reasons


def _category_tokens(category: str) -> set[str]:
    """Words of a free-text category, singularized enough to match the token sets.

    Categories are model-written phrases ("fresh vegetables", "aged hard cheese"),
    so matching is by contained word, with a crude plural trim that is fine for
    food-group nouns.
    """
    tokens = set[str]()
    for token in re.split(r"[^a-z]+", category.casefold()):
        if not token:
            continue
        tokens.add(token)
        if len(token) > 3 and token.endswith("s"):
            tokens.add(token[:-1])
    return tokens
