"""Unit tests for the composer's safety gate, with no database.

``verify_meal`` is a pure function over the index readings the composer gathered,
so the rules that actually decide whether a meal is safe are tested here with
hand-built lookups and term matchers, not a seeded Postgres.
"""

from app.agents.meal_verification import verify_meal
from app.core.term_match import TermMatcher
from app.services.ingredient_lookup import LookupCandidate, LookupResult


def _found(ingredient: str, compatibility: str) -> LookupResult:
    return LookupResult(
        ingredient=ingredient,
        found=True,
        ambiguous=False,
        matched_on="ingredient",
        error=None,
        candidates=[
            LookupCandidate(
                name=ingredient,
                compatibility=compatibility,
                mechanisms=(),
                category=None,
                notes=None,
            )
        ],
    )


def _missing(ingredient: str) -> LookupResult:
    return LookupResult(
        ingredient=ingredient,
        found=False,
        ambiguous=False,
        matched_on=None,
        error=None,
        candidates=[],
    )


def _errored(ingredient: str) -> LookupResult:
    return LookupResult(
        ingredient=ingredient,
        found=False,
        ambiguous=False,
        matched_on=None,
        error="index lookup failed",
        candidates=[],
    )


_NO_TERMS = TermMatcher.from_terms([])


def test_all_well_tolerated_is_safe() -> None:
    result = verify_meal([_found("courgette", "well_tolerated")], [], _NO_TERMS)

    assert result.is_safe
    assert result.blockers == []
    assert result.unverified == []


def test_incompatible_ingredient_blocks() -> None:
    result = verify_meal([_found("parmesan", "incompatible")], [], _NO_TERMS)

    assert not result.is_safe
    assert result.blockers == [("parmesan", "avoid")]


def test_moderately_compatible_blocks_as_depends() -> None:
    result = verify_meal([_found("spinach", "moderately_compatible")], [], _NO_TERMS)

    assert result.blockers == [("spinach", "depends")]


def test_errored_lookup_blocks_as_unverifiable() -> None:
    result = verify_meal([_errored("mystery")], [], _NO_TERMS)

    assert result.blockers == [("mystery", "unverifiable")]


def test_missing_ingredient_passes_but_is_recorded() -> None:
    result = verify_meal(
        [_found("courgette", "well_tolerated"), _missing("dragon fruit")], [], _NO_TERMS
    )

    assert result.is_safe
    assert result.unverified == ["dragon fruit"]


def test_found_but_unrated_is_recorded_not_waved_through() -> None:
    # In the index but with no rating (NULL compatibility surfaces as "unknown"):
    # unknown is not safe, so it joins the unverified list instead of passing silently.
    result = verify_meal(
        [_found("courgette", "well_tolerated"), _found("mystery herb", "unknown")], [], _NO_TERMS
    )

    assert result.is_safe
    assert result.blockers == []
    assert result.unverified == ["mystery herb"]


def test_risky_term_in_recipe_is_flagged() -> None:
    risky = TermMatcher.from_terms(["red wine", "parmesan"])

    result = verify_meal(
        [_found("courgette", "well_tolerated")],
        ["Saute the courgette.", "Deglaze with red wine."],
        risky,
    )

    assert not result.is_safe
    assert result.recipe_flags == ["red wine"]


def test_recipe_flags_are_deduped_across_steps() -> None:
    risky = TermMatcher.from_terms(["parmesan"])

    result = verify_meal(
        [_found("courgette", "well_tolerated")],
        ["Grate parmesan over the top.", "Serve with extra parmesan."],
        risky,
    )

    assert result.recipe_flags == ["parmesan"]


def test_safe_ingredient_in_recipe_does_not_flag() -> None:
    risky = TermMatcher.from_terms(["parmesan"])

    result = verify_meal(
        [_found("courgette", "well_tolerated")], ["Saute the courgette in olive oil."], risky
    )

    assert result.is_safe
