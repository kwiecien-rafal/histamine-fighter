"""The curated-index read behind dish assessment.

One plain async function shapes an ingredient lookup (with its category
fallback) into a self-describing result dict. It never raises: bad input or a
database blip on one ingredient comes back as a readable "no usable data"
result, so a single failure cannot abort a whole multi-ingredient assessment.
"""

from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.enums import CompatibilityVerdict
from app.services.ingredient_service import IngredientService, is_ambiguous

log = structlog.get_logger(__name__)


def _unusable(ingredient: str, error: str) -> dict[str, Any]:
    """A result that signals 'no usable data' without raising at the caller."""
    return {
        "ingredient": ingredient,
        "found": False,
        "ambiguous": False,
        "matched_on": None,
        "error": error,
        "candidates": [],
    }


async def lookup_ingredient_safety(
    service: IngredientService, ingredient: str, category: str | None = None
) -> dict[str, Any]:
    """Look up one ingredient's histamine compatibility in the curated index.

    The ingredient's own entry is authoritative; when it is not indexed and a
    ``category`` descriptor is given ("aged hard cheese" for parmesan), the
    lookup falls back to that category's entry. The index records
    histamine-relevant foods, so a miss means no known concern, not danger.

    Args:
        service: Request-scoped service reading the curated index.
        ingredient: A single ingredient name, not a phrase or dish.
        category: Short food-group + preparation descriptor for the fallback.

    Returns:
        A dict with ``ingredient`` (echoed), ``found``, ``ambiguous`` (true when
        entries disagree, e.g. egg yolk vs egg white), ``matched_on``
        (``"ingredient"``, ``"category"``, or ``None``), ``error`` (set only
        when the lookup could not be completed — treat the ingredient as
        unknown) and ``candidates``: the matching entries, each carrying
        ``name``, ``compatibility``, ``mechanisms``, ``category`` and ``notes``.
    """
    query = ingredient.strip()
    if not query:
        return _unusable(ingredient, "Provide a single ingredient name.")
    if len(query) > IngredientService.max_query_length:
        return _unusable(ingredient, "Too long — pass a single ingredient, not a phrase or dish.")

    try:
        matches = await service.find_candidates(ingredient)
        matched_on = "ingredient" if matches else None
        if not matches and category and category.strip():
            matches = await service.find_category_candidates(category)
            matched_on = "category" if matches else None
    except SQLAlchemyError:
        log.warning("ingredient.lookup.failed", ingredient=query[:60], exc_info=True)
        return _unusable(ingredient, "Index lookup failed; treat this ingredient as unknown.")

    return {
        "ingredient": ingredient,
        "found": bool(matches),
        "ambiguous": is_ambiguous(matches),
        "matched_on": matched_on,
        "error": None,
        "candidates": [
            {
                "name": match.ingredient.name,
                "compatibility": CompatibilityVerdict.from_compatibility(
                    match.ingredient.compatibility
                ).value,
                "mechanisms": [mechanism.value for mechanism in match.ingredient.mechanisms],
                "category": match.ingredient.category,
                "notes": match.ingredient.notes,
            }
            for match in matches
        ],
    }
