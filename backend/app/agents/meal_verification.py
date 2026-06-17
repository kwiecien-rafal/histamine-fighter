"""The code-owned safety check for a composed meal.

A pure function over the index readings the composer already gathered, so the
gate that decides whether a meal is safe can be unit-tested without a database.
It owns the verdict the same way the dish lookup does: an ``avoid``/``depends``
reading (or one that could not be read) blocks; an ingredient absent from the
index is *unknown*, not safe, so it passes the automated gate but is recorded for
the admin to clear; and the recipe prose is scanned for any index-flagged term
the model wrote into the steps but kept off the verified list.
"""

from dataclasses import dataclass

from app.agents.dish_lookup import _grounded_verdict
from app.core.term_match import TermMatcher
from app.enums import SafetyLevel
from app.services.ingredient_lookup import LookupResult


@dataclass(frozen=True, slots=True)
class MealVerification:
    """The outcome of checking a submitted meal against the curated index."""

    blockers: list[tuple[str, str]]
    unverified: list[str]
    recipe_flags: list[str]

    @property
    def is_safe(self) -> bool:
        return not self.blockers and not self.recipe_flags


def verify_meal(
    lookups: list[LookupResult], recipe_steps: list[str], risky_terms: TermMatcher
) -> MealVerification:
    """Classify each ingredient reading and scan the recipe for risky mentions.

    Args:
        lookups: One reading per submitted ingredient, in the submitted order.
        recipe_steps: The normalized recipe steps to scan for risky terms.
        risky_terms: The index's worse-than-safe terms, prepared for matching.
    """
    blockers: list[tuple[str, str]] = []
    unverified: list[str] = []
    for lookup in lookups:
        if lookup.error:
            blockers.append((lookup.ingredient, "unverifiable"))
        elif not lookup.found:
            unverified.append(lookup.ingredient)
        else:
            level = _grounded_verdict([lookup])
            if level is not SafetyLevel.SAFE:
                blockers.append((lookup.ingredient, level.value))

    recipe_flags: list[str] = []
    seen: set[str] = set()
    for step in recipe_steps:
        for term in risky_terms.found_in(step):
            if term not in seen:
                seen.add(term)
                recipe_flags.append(term)

    return MealVerification(blockers=blockers, unverified=unverified, recipe_flags=recipe_flags)
