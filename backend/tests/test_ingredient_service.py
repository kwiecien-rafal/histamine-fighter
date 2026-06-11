"""Tests for IngredientService.find_candidates."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import Compatibility, MatchType
from app.models import HistamineIngredient
from app.services.ingredient_service import (
    IngredientMatch,
    IngredientService,
    is_ambiguous,
)


def _ingredient(name: str, **kwargs: object) -> HistamineIngredient:
    """Build an ingredient; the model derives the normalized lookup keys."""
    return HistamineIngredient(name=name, sources=["test source"], **kwargs)


def _match(compatibility: Compatibility | None) -> IngredientMatch:
    return IngredientMatch(_ingredient("x", compatibility=compatibility), MatchType.FUZZY, 0.5)


async def test_exact_returns_single_candidate(session: AsyncSession) -> None:
    session.add(_ingredient("Tomato", compatibility=Compatibility.INCOMPATIBLE))
    await session.flush()

    candidates = await IngredientService(session).find_candidates("  ToMaTo ")
    assert [c.match_type for c in candidates] == [MatchType.EXACT]
    assert candidates[0].ingredient.name == "Tomato"
    assert candidates[0].score == 1.0


async def test_alias_match(session: AsyncSession) -> None:
    session.add(_ingredient("Eggplant", aliases=["Aubergine", "brinjal"]))
    await session.flush()

    candidates = await IngredientService(session).find_candidates("aubergine")
    assert candidates[0].ingredient.name == "Eggplant"
    assert candidates[0].match_type is MatchType.ALIAS


async def test_fuzzy_handles_typos(session: AsyncSession) -> None:
    session.add(_ingredient("Cheddar"))
    await session.flush()

    candidates = await IngredientService(session).find_candidates("chedar")
    assert candidates[0].ingredient.name == "Cheddar"
    assert candidates[0].match_type is MatchType.FUZZY


async def test_ambiguous_query_surfaces_the_risky_reading(
    session: AsyncSession,
) -> None:
    # "egg" must not hide egg white behind egg yolk: both have to be returned so
    # the caller can see the conflict instead of being told it is safe.
    session.add(_ingredient("Egg Yolk", compatibility=Compatibility.WELL_TOLERATED))
    session.add(_ingredient("Egg White", compatibility=Compatibility.INCOMPATIBLE))
    session.add(_ingredient("Whole Egg", compatibility=Compatibility.INCOMPATIBLE))
    await session.flush()

    candidates = await IngredientService(session).find_candidates("egg")
    names = {c.ingredient.name for c in candidates}
    assert {"Egg Yolk", "Egg White"} <= names
    verdicts = {c.ingredient.compatibility for c in candidates}
    assert Compatibility.INCOMPATIBLE in verdicts


async def test_relevance_cutoff_drops_weaker_fuzzy(session: AsyncSession) -> None:
    # Tomato Juice clears the floor for "tomatos" but is well below the best hit,
    # so the relative cutoff drops it. Note: this leans on pg_trgm's scores
    # (~0.667 vs ~0.444 straddling the 0.75 ratio) and could shift if pg_trgm does.
    session.add(_ingredient("Tomato"))
    session.add(_ingredient("Tomato Juice"))
    await session.flush()

    names = {c.ingredient.name for c in await IngredientService(session).find_candidates("tomatos")}
    assert "Tomato" in names
    assert "Tomato Juice" not in names


async def test_results_are_deterministically_ordered(session: AsyncSession) -> None:
    # Equal score (shared alias, same verdict) must still order stably, by name.
    session.add(_ingredient("Beta Cheese", aliases=["qx"]))
    session.add(_ingredient("Alpha Cheese", aliases=["qx"]))
    await session.flush()

    candidates = await IngredientService(session).find_candidates("qx")
    assert [c.ingredient.name for c in candidates] == ["Alpha Cheese", "Beta Cheese"]


async def test_unknown_returns_empty(session: AsyncSession) -> None:
    session.add(_ingredient("Tomato"))
    await session.flush()

    assert await IngredientService(session).find_candidates("xyzzyqwerty") == []


async def test_blank_query_returns_empty(session: AsyncSession) -> None:
    assert await IngredientService(session).find_candidates("   ") == []


async def test_overlong_query_returns_empty(session: AsyncSession) -> None:
    # The agent path has no other length guard; oversized input is never a real
    # ingredient and must not reach the trigram scan.
    assert await IngredientService(session).find_candidates("x" * 500) == []


async def test_exact_name_short_circuits_even_for_an_ambiguous_term(
    session: AsyncSession,
) -> None:
    # Documents the contract: an exact canonical-name hit returns only that row,
    # so curated data must avoid bare ambiguous names (model "egg" as Whole Egg
    # plus an "egg" alias) or the variant below would be suppressed.
    session.add(_ingredient("Egg", compatibility=Compatibility.WELL_TOLERATED))
    session.add(_ingredient("Egg White", compatibility=Compatibility.INCOMPATIBLE))
    await session.flush()

    candidates = await IngredientService(session).find_candidates("egg")
    assert [c.ingredient.name for c in candidates] == ["Egg"]


# --- find_category_candidates (umbrella rows, exact match only) ------------------


def _category_row(name: str, **kwargs: object) -> HistamineIngredient:
    return _ingredient(name, is_category=True, **kwargs)


async def test_category_matches_an_umbrella_row_by_name(session: AsyncSession) -> None:
    session.add(_category_row("Hard Cheese", compatibility=Compatibility.POORLY_TOLERATED))
    await session.flush()

    candidates = await IngredientService(session).find_category_candidates(" Hard  Cheese ")
    assert [c.ingredient.name for c in candidates] == ["Hard Cheese"]
    assert candidates[0].match_type is MatchType.EXACT


async def test_category_matches_an_umbrella_row_by_alias(session: AsyncSession) -> None:
    session.add(_category_row("Hard Cheese", aliases=["aged hard cheese"]))
    await session.flush()

    candidates = await IngredientService(session).find_category_candidates("aged hard cheese")
    assert [c.ingredient.name for c in candidates] == ["Hard Cheese"]
    assert candidates[0].match_type is MatchType.ALIAS


async def test_category_never_matches_an_ordinary_row(session: AsyncSession) -> None:
    # Tomato is a specific ingredient, not a curated umbrella; a category
    # descriptor must not resolve through it.
    session.add(_ingredient("Tomato", compatibility=Compatibility.INCOMPATIBLE))
    await session.flush()

    assert await IngredientService(session).find_category_candidates("tomato") == []


async def test_category_matching_has_no_fuzzy(session: AsyncSession) -> None:
    # Free-text descriptors must not drift into nearby rows ("meat" -> cured
    # meats); a near-miss is a miss, falling back to today's behaviour.
    session.add(_category_row("Hard Cheese"))
    await session.flush()

    assert await IngredientService(session).find_category_candidates("hard chese") == []


async def test_blank_category_returns_empty(session: AsyncSession) -> None:
    assert await IngredientService(session).find_category_candidates("   ") == []


async def test_overlong_category_returns_empty(session: AsyncSession) -> None:
    assert await IngredientService(session).find_category_candidates("x" * 500) == []


def test_model_derives_normalized_keys_from_name_and_aliases() -> None:
    # The matcher relies on these keys; the model derives them so no caller has
    # to set them by hand and risk them drifting from name/aliases.
    ingredient = _ingredient("  Aged   Parmesan ", aliases=["Grana Padano", "  PARMIGIANO "])
    assert ingredient.normalized_name == "aged parmesan"
    assert ingredient.normalized_aliases == ["grana padano", "parmigiano"]


def test_is_ambiguous_when_verdicts_differ() -> None:
    assert is_ambiguous([_match(Compatibility.WELL_TOLERATED), _match(Compatibility.INCOMPATIBLE)])


def test_is_ambiguous_treats_unrated_as_a_distinct_verdict() -> None:
    assert is_ambiguous([_match(None), _match(Compatibility.INCOMPATIBLE)])


def test_is_not_ambiguous_when_verdicts_agree() -> None:
    matches = [_match(Compatibility.INCOMPATIBLE), _match(Compatibility.INCOMPATIBLE)]
    assert not is_ambiguous(matches)
