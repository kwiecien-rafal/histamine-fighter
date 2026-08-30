"""Tests for the dish-lookup caches: TTL reads, guarded writes, and the fingerprint.

The assessment tier's contract is the interesting one: a hit is only served
while its grounding fingerprint still matches the live index — any index drift,
even one that leaves the dish verdict unchanged, reads as a miss, so a seed
change can never surface a stale badge, note, or swap suggestion.

The rewrite tier holds that on both sides at once. Its row hands back a dish to
cook, so it stops being served when the index moves under either the list it was
asked about or the list it produced.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import Compatibility, RewriteOutcome, SafetyLevel
from app.models import (
    HistamineIngredient,
    LookupAssessmentCache,
    LookupProposalCache,
    LookupRewriteCache,
)
from app.schemas.meal import (
    AdaptedDish,
    ConfirmedIngredient,
    DishAssessmentResponse,
    IngredientAssessment,
    IngredientProposalResponse,
    ProposedIngredient,
)
from app.schemas.usage import LLMUsage
from app.services.ingredient_service import IngredientService
from app.services.lookup_cache_service import LookupCacheService, ingredients_hash


def _service(session: AsyncSession, ttl_days: int = 90) -> LookupCacheService:
    return LookupCacheService(session, IngredientService(session), ttl_days=ttl_days)


def _proposal(dish: str = "Spaghetti Bolognese", **kwargs: object) -> IngredientProposalResponse:
    defaults: dict[str, object] = {
        "dish": dish,
        "ingredients": [ProposedIngredient(name="tomato", category="vegetable")],
        "model": "stub/model",
        "usage": LLMUsage(),
    }
    return IngredientProposalResponse(**{**defaults, **kwargs})  # type: ignore[arg-type]


def _confirmed(name: str, category: str | None = None) -> ConfirmedIngredient:
    return ConfirmedIngredient(name=name, category=category)


def _assessment(
    verdict: SafetyLevel, ingredients: list[IngredientAssessment]
) -> DishAssessmentResponse:
    return DishAssessmentResponse(
        dish="Test Dish",
        verdict=verdict,
        explanation="because.",
        adaptations=[],
        advisories=[],
        integrity="preserved",  # type: ignore[arg-type]
        ingredients=ingredients,
        model="stub/model",
        usage=LLMUsage(),
    )


def _reading(name: str, safety: SafetyLevel, error: bool = False) -> IngredientAssessment:
    return IngredientAssessment(name=name, safety=safety, found=not error, error=error)


# --- proposal tier -----------------------------------------------------------------


async def test_proposal_round_trip_echoes_the_callers_dish(session: AsyncSession) -> None:
    service = _service(session)
    await service.store_proposal(_proposal("Spaghetti Bolognese"))

    hit = await service.get_proposal("  SPAGHETTI bolognese ")

    assert hit is not None
    assert hit.cached is True
    assert hit.dish == "  SPAGHETTI bolognese "  # caller's text, not the stored one
    assert hit.model == "stub/model"
    assert hit.usage.calls == 0
    assert [item.name for item in hit.ingredients] == ["tomato"]


async def test_expired_proposal_is_a_miss(session: AsyncSession) -> None:
    service = _service(session, ttl_days=0)
    await service.store_proposal(_proposal())

    assert await service.get_proposal("Spaghetti Bolognese") is None


async def test_unrecognized_and_empty_proposals_are_never_stored(session: AsyncSession) -> None:
    service = _service(session)
    await service.store_proposal(_proposal("gibberish", recognized=False))
    await service.store_proposal(_proposal("empty dish", ingredients=[]))

    rows = (await session.execute(select(LookupProposalCache))).scalars().all()
    assert rows == []


async def test_proposal_upsert_replaces_the_earlier_row(session: AsyncSession) -> None:
    service = _service(session)
    await service.store_proposal(_proposal())
    await service.store_proposal(
        _proposal(
            ingredients=[ProposedIngredient(name="beef", category="fresh meat")],
            model="newer/model",
        )
    )

    hit = await service.get_proposal("Spaghetti Bolognese")

    assert hit is not None
    assert hit.model == "newer/model"
    assert [item.name for item in hit.ingredients] == ["beef"]


# --- assessment tier ---------------------------------------------------------------


async def test_assessment_hit_serves_after_verdict_regrounds(session: AsyncSession) -> None:
    session.add(
        HistamineIngredient(
            name="Tomato", sources=["test"], compatibility=Compatibility.INCOMPATIBLE
        )
    )
    await session.flush()
    service = _service(session)
    ingredients = [_confirmed("tomato"), _confirmed("rice")]
    stored = _assessment(
        SafetyLevel.AVOID,
        [_reading("tomato", SafetyLevel.AVOID), _reading("rice", SafetyLevel.SAFE)],
    )
    await service.store_assessment("Pasta", ingredients, stored)

    hit = await service.get_assessment("Pasta", ingredients)

    assert hit is not None
    assert hit.cached is True
    assert hit.verdict is SafetyLevel.AVOID
    assert hit.usage.calls == 0


async def test_assessment_ingredient_order_does_not_matter(session: AsyncSession) -> None:
    service = _service(session)
    ingredients = [_confirmed("tomato"), _confirmed("rice")]
    await service.store_assessment(
        "Pasta", ingredients, _assessment(SafetyLevel.SAFE, [_reading("rice", SafetyLevel.SAFE)])
    )

    hit = await service.get_assessment("Pasta", list(reversed(ingredients)))

    assert hit is not None


async def test_assessment_misses_when_the_index_moved(session: AsyncSession) -> None:
    # Cached while tomato was unindexed (verdict safe); the index then gains an
    # incompatible Tomato row, so the re-grade disagrees and the row is a miss.
    service = _service(session)
    ingredients = [_confirmed("tomato")]
    await service.store_assessment(
        "Tomato Soup",
        ingredients,
        _assessment(SafetyLevel.SAFE, [_reading("tomato", SafetyLevel.SAFE)]),
    )
    session.add(
        HistamineIngredient(
            name="Tomato", sources=["test"], compatibility=Compatibility.INCOMPATIBLE
        )
    )
    await session.flush()

    assert await service.get_assessment("Tomato Soup", ingredients) is None


async def test_assessment_with_errored_reading_is_never_stored(session: AsyncSession) -> None:
    service = _service(session)
    ingredients = [_confirmed("tomato")]
    floored = _assessment(
        SafetyLevel.DEPENDS, [_reading("tomato", SafetyLevel.DEPENDS, error=True)]
    )
    await service.store_assessment("Pasta", ingredients, floored)

    rows = (await session.execute(select(LookupAssessmentCache))).scalars().all()
    assert rows == []


async def test_different_ingredient_sets_key_separately(session: AsyncSession) -> None:
    service = _service(session)
    await service.store_assessment(
        "Pasta",
        [_confirmed("tomato")],
        _assessment(SafetyLevel.SAFE, [_reading("tomato", SafetyLevel.SAFE)]),
    )

    assert await service.get_assessment("Pasta", [_confirmed("basil")]) is None


async def test_assessment_misses_on_index_drift_that_leaves_the_verdict_alone(
    session: AsyncSession,
) -> None:
    # The basil case: tomato alone already makes the dish avoid; basil then
    # flips from unindexed to incompatible. The dish verdict is avoid either
    # way, but the cached response still shows basil's old safe badge, so the
    # fingerprint must read the row as a miss.
    session.add(
        HistamineIngredient(
            name="Tomato", sources=["test"], compatibility=Compatibility.INCOMPATIBLE
        )
    )
    await session.flush()
    service = _service(session)
    ingredients = [_confirmed("tomato"), _confirmed("basil")]
    stored = _assessment(
        SafetyLevel.AVOID,
        [_reading("tomato", SafetyLevel.AVOID), _reading("basil", SafetyLevel.SAFE)],
    )
    await service.store_assessment("Pasta", ingredients, stored)
    assert await service.get_assessment("Pasta", ingredients) is not None

    session.add(
        HistamineIngredient(
            name="Basil", sources=["test"], compatibility=Compatibility.INCOMPATIBLE
        )
    )
    await session.flush()

    assert await service.get_assessment("Pasta", ingredients) is None


async def test_assessment_misses_when_a_substitute_option_changes(
    session: AsyncSession,
) -> None:
    # Adaptation prose can name index-grounded swap options for an avoid-level
    # ingredient's category, so a new well-tolerated row in that category is
    # grounding drift too, even though no confirmed ingredient's reading moved.
    session.add(
        HistamineIngredient(
            name="Parmesan",
            sources=["test"],
            compatibility=Compatibility.INCOMPATIBLE,
            category="cheese",
        )
    )
    await session.flush()
    service = _service(session)
    ingredients = [_confirmed("parmesan")]
    stored = _assessment(SafetyLevel.AVOID, [_reading("parmesan", SafetyLevel.AVOID)])
    await service.store_assessment("Risotto", ingredients, stored)
    assert await service.get_assessment("Risotto", ingredients) is not None

    session.add(
        HistamineIngredient(
            name="Ricotta",
            sources=["test"],
            compatibility=Compatibility.WELL_TOLERATED,
            category="cheese",
        )
    )
    await session.flush()

    assert await service.get_assessment("Risotto", ingredients) is None


def test_ingredients_hash_is_order_and_case_insensitive() -> None:
    first = ingredients_hash([_confirmed("Tomato", "Vegetable"), _confirmed("rice")])
    second = ingredients_hash([_confirmed("rice"), _confirmed("tomato", "vegetable")])
    different = ingredients_hash([_confirmed("tomato", "fruit"), _confirmed("rice")])

    assert first == second
    assert first != different  # the category is part of the identity


def test_ingredients_hash_cannot_collide_on_delimiter_characters() -> None:
    # "a|b" with category "c" and "a" with category "b|c" concatenate the same;
    # the JSON encoding must keep them distinct sets.
    first = ingredients_hash([_confirmed("a|b", "c")])
    second = ingredients_hash([_confirmed("a", "b|c")])

    assert first != second


# --- rewrite tier ------------------------------------------------------------------


def _adapted(
    outcome: RewriteOutcome = RewriteOutcome.ADAPTED,
    ingredients: list[str] = ["courgette"],
) -> AdaptedDish:
    return AdaptedDish(
        dish="Spaghetti Bolognese",
        name="Spaghetti with Courgette",
        outcome=outcome,
        explanation="Courgette carries the sauce.",
        ingredients=[ProposedIngredient(name=name) for name in ingredients],
        verdict=SafetyLevel.SAFE,
        model="stub/model",
        usage=LLMUsage(),
    )


async def test_rewrite_round_trip_serves_while_both_sides_hold(session: AsyncSession) -> None:
    service = _service(session)
    asked = [_confirmed("tomato"), _confirmed("basil")]
    await service.store_rewrite("Pasta", asked, _adapted())

    hit = await service.get_rewrite("Pasta", asked)

    assert hit is not None
    assert hit.cached is True
    assert hit.name == "Spaghetti with Courgette"
    assert hit.usage.calls == 0


async def test_rewrite_misses_when_the_index_moves_under_the_new_dish(
    session: AsyncSession,
) -> None:
    # Cached while courgette was unindexed; the index then flags it. The row still
    # describes a dish we would be telling someone to cook, so it must not serve.
    service = _service(session)
    asked = [_confirmed("tomato")]
    await service.store_rewrite("Pasta", asked, _adapted())
    session.add(
        HistamineIngredient(
            name="Courgette", sources=["test"], compatibility=Compatibility.INCOMPATIBLE
        )
    )
    await session.flush()

    assert await service.get_rewrite("Pasta", asked) is None


async def test_rewrite_misses_when_the_index_moves_under_the_original(
    session: AsyncSession,
) -> None:
    # The other side of the fingerprint: what the rewrite decisions were drawn from
    # has changed, so the version it produced is no longer the answer to the question.
    service = _service(session)
    asked = [_confirmed("tomato")]
    await service.store_rewrite("Pasta", asked, _adapted())
    session.add(
        HistamineIngredient(
            name="Tomato", sources=["test"], compatibility=Compatibility.INCOMPATIBLE
        )
    )
    await session.flush()

    assert await service.get_rewrite("Pasta", asked) is None


async def test_only_an_adapted_outcome_is_worth_a_row(session: AsyncSession) -> None:
    service = _service(session)
    asked = [_confirmed("tomato")]

    for outcome in (
        RewriteOutcome.UNCHANGED,
        RewriteOutcome.IMPOSSIBLE,
        RewriteOutcome.EXHAUSTED,
    ):
        await service.store_rewrite("Pasta", asked, _adapted(outcome, ingredients=[]))

    # None of the three cost a model call worth freezing, and pinning an exhausted
    # run would deny a second attempt that might well succeed.
    rows = (await session.execute(select(LookupRewriteCache))).scalars().all()
    assert rows == []


async def test_rewrite_keys_on_the_list_it_was_asked_about(session: AsyncSession) -> None:
    service = _service(session)
    await service.store_rewrite("Pasta", [_confirmed("tomato")], _adapted())

    assert await service.get_rewrite("Pasta", [_confirmed("tomato"), _confirmed("beef")]) is None
