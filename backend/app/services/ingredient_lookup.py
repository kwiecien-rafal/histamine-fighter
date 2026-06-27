"""The curated-index read behind dish assessment.

Shapes ingredient lookups (with their category fallback) into self-describing
:class:`LookupResult` value objects. Neither path raises: bad input or a
database blip on one ingredient comes back as a readable "no usable data"
result, so a single failure cannot abort a whole multi-ingredient assessment.

``lookup_ingredients`` is the batched orchestrator the dish agent uses; it and
the single-ingredient ``lookup_ingredient_safety`` share one assembly step, so
the two paths can never describe the same row differently.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.core.term_match import TermMatcher
from app.enums import CompatibilityVerdict, HistamineMechanism
from app.schemas.meal import ProposedIngredient
from app.services.ingredient_service import (
    IngredientMatch,
    IngredientService,
    is_ambiguous,
)

if TYPE_CHECKING:
    from app.agents.meal_verification import MealVerification

log = structlog.get_logger(__name__)

_EMPTY_INPUT = "Provide a single ingredient name."
_OVERLONG_INPUT = "Too long — pass a single ingredient, not a phrase or dish."
_LOOKUP_FAILED = "Index lookup failed; treat this ingredient as unknown."


@dataclass(frozen=True, slots=True)
class LookupCandidate:
    """One matched index row, flattened to the fields the agent reasons over."""

    name: str
    compatibility: str
    mechanisms: tuple[HistamineMechanism, ...]
    category: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class LookupResult:
    """One ingredient's reading from the curated index.

    ``found`` is false when the index has no entry; ``ambiguous`` is true when
    the candidates disagree (egg yolk vs egg white); ``matched_on`` records the
    tier that hit; ``error`` is set only when the lookup could not be completed,
    in which case the ingredient must be treated as unknown, never as safe.
    """

    ingredient: str
    found: bool
    ambiguous: bool
    matched_on: Literal["ingredient", "category"] | None
    error: str | None
    candidates: list[LookupCandidate]


def _unusable(ingredient: str, error: str) -> LookupResult:
    """A result that signals 'no usable data' without raising at the caller."""
    return LookupResult(
        ingredient=ingredient,
        found=False,
        ambiguous=False,
        matched_on=None,
        error=error,
        candidates=[],
    )


def _candidate(match: IngredientMatch) -> LookupCandidate:
    row = match.ingredient
    return LookupCandidate(
        name=row.name,
        compatibility=CompatibilityVerdict.from_compatibility(row.compatibility).value,
        mechanisms=tuple(row.mechanisms),
        category=row.category,
        notes=row.notes,
    )


def _assemble_result(
    ingredient: str,
    matches: list[IngredientMatch],
    matched_on: Literal["ingredient", "category"] | None,
) -> LookupResult:
    """Build the result both paths return, so neither can describe a row its own way."""
    return LookupResult(
        ingredient=ingredient,
        found=bool(matches),
        ambiguous=is_ambiguous(matches),
        matched_on=matched_on,
        error=None,
        candidates=[_candidate(match) for match in matches],
    )


async def _resolve_with_category(
    service: IngredientService,
    ingredient: str,
    category: str | None,
    primary: list[IngredientMatch],
) -> LookupResult:
    """Assemble from the primary matches, falling back to the category on a miss.

    The ingredient's own entry is authoritative; only when it misses and a
    ``category`` descriptor is given ("aged hard cheese" for parmesan) does the
    lookup fall back to that category's umbrella row.
    """
    if primary:
        return _assemble_result(ingredient, primary, "ingredient")
    if category and category.strip():
        fallback = await service.find_category_candidates(category)
        if fallback:
            return _assemble_result(ingredient, fallback, "category")
    return _assemble_result(ingredient, primary, None)


async def lookup_ingredient_safety(
    service: IngredientService, ingredient: str, category: str | None = None
) -> LookupResult:
    """Look up one ingredient's histamine compatibility in the curated index.

    The index records histamine-relevant foods, so a miss means no known
    concern, not danger. Kept as the single-ingredient entry point (and its
    tests); the dish agent uses :func:`lookup_ingredients` for the whole list.

    Args:
        service: Request-scoped service reading the curated index.
        ingredient: A single ingredient name, not a phrase or dish.
        category: Short food-group + preparation descriptor for the fallback.

    Returns:
        A :class:`LookupResult`. On invalid input or a database blip it carries
        ``error`` and no candidates, never raising at the caller.
    """
    query = ingredient.strip()
    if not query:
        return _unusable(ingredient, _EMPTY_INPUT)
    if len(query) > IngredientService.max_query_length:
        return _unusable(ingredient, _OVERLONG_INPUT)

    try:
        primary = await service.find_candidates(ingredient)
        return await _resolve_with_category(service, ingredient, category, primary)
    except SQLAlchemyError:
        log.warning("ingredient.lookup.failed", ingredient=query[:60], exc_info=True)
        return _unusable(ingredient, _LOOKUP_FAILED)


async def lookup_ingredients(
    service: IngredientService, items: Sequence[tuple[str, str | None]]
) -> list[LookupResult]:
    """Read a whole confirmed list, batching the common primary tier.

    One :meth:`IngredientService.find_candidates_many` resolves every name's
    primary match in ~2 queries; a per-miss category fallback then runs serially
    for the items that missed and carry a category (the rare cold path). The
    cautious direction governs the failures: invalid input degrades that one
    item; a category-fallback blip degrades that one item; a failure of the
    batched primary query degrades every item it covered. Results stay in the
    order of ``items``.
    """
    results: list[LookupResult | None] = [None] * len(items)
    pending: list[int] = []
    for index, (ingredient, _category) in enumerate(items):
        query = ingredient.strip()
        if not query:
            results[index] = _unusable(ingredient, _EMPTY_INPUT)
        elif len(query) > IngredientService.max_query_length:
            results[index] = _unusable(ingredient, _OVERLONG_INPUT)
        else:
            pending.append(index)

    if pending:
        names = [items[index][0] for index in pending]
        try:
            matches_by_name = await service.find_candidates_many(names)
        except SQLAlchemyError:
            log.warning("ingredient.lookup.batch_failed", count=len(names), exc_info=True)
            for index in pending:
                results[index] = _unusable(items[index][0], _LOOKUP_FAILED)
        else:
            for index in pending:
                ingredient, category = items[index]
                try:
                    results[index] = await _resolve_with_category(
                        service, ingredient, category, matches_by_name.get(ingredient, [])
                    )
                except SQLAlchemyError:
                    log.warning(
                        "ingredient.lookup.failed", ingredient=ingredient[:60], exc_info=True
                    )
                    results[index] = _unusable(ingredient, _LOOKUP_FAILED)

    return [result for result in results if result is not None]


async def verify_submission(
    service: IngredientService,
    ingredients: Sequence[ProposedIngredient],
    recipe: Sequence[str] | None,
    *,
    risky_terms: TermMatcher,
) -> "MealVerification":
    """Re-derive a meal's safety from the index: the shared composer/edit gate.

    Reads every ingredient against the curated index and scans the recipe for an
    index-flagged term, so a composition and an admin edit are vetted identically and
    can never produce different verdicts for the same list. ``meal_verification`` is
    imported inside the function on purpose: it depends on this module's
    ``LookupResult``, so a module-level import here would cycle.
    """
    from app.agents.meal_verification import verify_meal

    lookups = await lookup_ingredients(
        service, [(item.name, item.category) for item in ingredients]
    )
    return verify_meal(lookups, list(recipe or []), risky_terms)
