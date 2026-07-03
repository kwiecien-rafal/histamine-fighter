"""Unit tests for the code-owned inspiration sampler.

The sampler is where the composer's variety comes from, so what is pinned here is
that the entropy is deterministic under an injected rng, that every anchor pool is
drawn from, and that the brief renders the lines the compose prompt embeds.
"""

import random

from app.agents.inspiration import CulinaryAnchors, InspirationBrief, load_anchors, sample_brief
from app.enums import MealType

_ANCHORS = CulinaryAnchors(
    cuisines=["Nordic", "Levantine"],
    techniques=["roasted", "steamed"],
    flavor_profiles=["herby", "zesty"],
    formats={
        MealType.BREAKFAST: ["a porridge"],
        MealType.LUNCH: ["a grain bowl"],
        MealType.DINNER: ["a traybake", "a stew"],
        MealType.SNACK: ["a dip"],
    },
)


def test_same_seed_draws_the_same_brief() -> None:
    first = sample_brief(
        MealType.DINNER,
        hero_pool=["fennel", "courgette"],
        rng=random.Random("2026-07-04:dinner:0"),
        anchors=_ANCHORS,
    )
    second = sample_brief(
        MealType.DINNER,
        hero_pool=["fennel", "courgette"],
        rng=random.Random("2026-07-04:dinner:0"),
        anchors=_ANCHORS,
    )

    assert first == second


def test_a_different_attempt_seed_can_change_the_draw() -> None:
    draws = {
        sample_brief(
            MealType.DINNER,
            hero_pool=["fennel", "courgette", "carrot", "parsnip"],
            rng=random.Random(f"2026-07-04:dinner:{attempt}"),
            anchors=_ANCHORS,
        ).model_dump_json()
        for attempt in range(6)
    }

    assert len(draws) > 1


def test_brief_draws_from_the_meal_type_format_pool() -> None:
    brief = sample_brief(MealType.LUNCH, hero_pool=[], rng=random.Random(1), anchors=_ANCHORS)

    assert brief.dish_format == "a grain bowl"
    assert brief.cuisine in _ANCHORS.cuisines
    assert brief.technique in _ANCHORS.techniques
    assert brief.flavor_profile in _ANCHORS.flavor_profiles


def test_empty_hero_pool_skips_the_hero() -> None:
    brief = sample_brief(MealType.SNACK, hero_pool=[], rng=random.Random(1), anchors=_ANCHORS)

    assert brief.hero_ingredient is None
    assert "Hero ingredient" not in brief.prompt_lines()


def test_prompt_lines_carry_the_draw_and_the_avoid_list() -> None:
    brief = InspirationBrief(
        cuisine="Nordic",
        technique="roasted",
        dish_format="a traybake",
        flavor_profile="herby",
        hero_ingredient="fennel",
        avoid_names=["Millet porridge", "Oat pancakes"],
    )

    lines = brief.prompt_lines()
    assert "Cuisine direction: Nordic" in lines
    assert "Hero ingredient, already verified well tolerated: fennel" in lines
    assert "Millet porridge; Oat pancakes" in lines
    assert "starting point, not a straitjacket" in lines


def test_seed_file_loads_with_a_format_pool_per_meal_type() -> None:
    anchors = load_anchors()

    assert set(anchors.formats) == set(MealType)
    assert all(pool for pool in anchors.formats.values())
    assert anchors.cuisines and anchors.techniques and anchors.flavor_profiles
