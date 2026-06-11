"""Database-backed tools for the dish-lookup agent.

Tools are built per request and closed over a request-scoped
:class:`IngredientService`, so they read the same session as the rest of the
request and never reach for a global. A tool's docstring is the contract the
model sees, so it spells out what the result means — above all that an unknown
ingredient is not a safe one.

The tool never raises: bad input or a database blip on one ingredient comes back
as a result the agent can read ("treat as unknown") so a single failure cannot
abort a whole multi-ingredient analysis.
"""

from typing import Any

import structlog
from langchain_core.tools import BaseTool, tool
from sqlalchemy.exc import SQLAlchemyError

from app.enums import CompatibilityVerdict
from app.services.ingredient_service import IngredientService, is_ambiguous

log = structlog.get_logger(__name__)


def _unusable(ingredient: str, error: str) -> dict[str, Any]:
    """A result that signals 'no usable data' without throwing out of the loop."""
    return {
        "ingredient": ingredient,
        "found": False,
        "ambiguous": False,
        "matched_on": None,
        "error": error,
        "candidates": [],
    }


def build_dish_lookup_tools(service: IngredientService) -> list[BaseTool]:
    """Build the tools the dish-lookup agent may call, bound to one DB session."""

    @tool
    async def lookup_ingredient_safety(
        ingredient: str, category: str | None = None
    ) -> dict[str, Any]:
        """Look up one ingredient's histamine compatibility in the curated index.

        Call this for every ingredient you consider, including any swap you plan
        to suggest. Pass a single ingredient name ("parmesan"), not a dish
        ("pasta with parmesan"). Also pass ``category``: a short descriptor of the
        food group and preparation style — "aged hard cheese" for parmesan,
        "smoked fish" for kippers, "citrus fruit" for yuzu. When the exact
        ingredient is not indexed, the lookup falls back to its category entry; a
        specific ingredient entry always wins over the category.

        The result has:
        - ``found``: false when neither the ingredient nor its category is in the
          curated index. The index records histamine-relevant foods (mostly ones
          to avoid, some noted as well tolerated), so an ingredient absent with no
          matching category has no known concern — treat it as fine, not as risky.
        - ``matched_on``: ``"ingredient"`` when the entries are for the food
          itself, ``"category"`` when the food matched only as a member of a
          broader indexed group — explain it that way.
        - ``ambiguous``: true when the name maps to entries that disagree (egg yolk
          vs egg white); treat the dish cautiously or say which reading you assumed.
        - ``error``: present only when the call could not be completed (bad input or
          a lookup failure); treat the ingredient as unknown and carry on.
        - ``candidates``: the matching entries, each with:
            - ``compatibility``: one of ``well_tolerated``, ``moderately_compatible``,
              ``incompatible``, ``poorly_tolerated``, ``unknown`` — grounds the verdict.
            - ``mechanisms``: why it may trigger symptoms (e.g. ``high_histamine``,
              ``dao_blocker``, ``liberator``). Base your explanation on these; do
              not invent reasons.
            - ``category``: e.g. ``cheese`` — use it to choose a same-category safe swap.
            - ``notes``: a short plain-language note, or null.
        """
        query = ingredient.strip()
        if not query:
            return _unusable(ingredient, "Provide a single ingredient name.")
        if len(query) > IngredientService.max_query_length:
            return _unusable(
                ingredient, "Too long — pass a single ingredient, not a phrase or dish."
            )

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

    return [lookup_ingredient_safety]
