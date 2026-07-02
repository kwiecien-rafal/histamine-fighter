"""The code-owned safety check for a composed meal.

A pure function over the index readings the composer already gathered, so the
gate that decides whether a meal is safe can be unit-tested without a database.
It owns the verdict the same way the dish lookup does: an ``avoid``/``depends``
reading (or one that could not be read) blocks; an ingredient the index cannot
vouch for is *unknown*, not safe, so it passes the automated gate but is recorded
for the admin to clear. When a ``risky_terms`` matcher is supplied (the composer
path), the recipe prose is also scanned for any index-flagged term the model wrote
into the steps but kept off the verified list; the admin edit gate skips the scan
so a human note like "fine in moderation" is not rejected for naming a term.

"Cannot vouch for" covers two cases the rest of the app keeps apart from safe: a
name with no index entry, and a name the index lists but never rated (a NULL
compatibility). Both are recorded as unverified rather than waved through.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.dish_lookup import _grounded_verdict
from app.core.term_match import TermMatcher
from app.enums import CompatibilityVerdict, SafetyLevel, TraceReading
from app.services.ingredient_lookup import LookupResult

# Only non-safe levels reach a blocker; safe never blocks, so the map is total here.
_LEVEL_READING = {
    SafetyLevel.DEPENDS: TraceReading.DEPENDS,
    SafetyLevel.AVOID: TraceReading.AVOID,
}


@dataclass(frozen=True, slots=True)
class MealVerification:
    """The outcome of checking a submitted meal against the curated index."""

    blockers: list[tuple[str, TraceReading]]
    unverified: list[str]
    recipe_flags: list[str]

    @property
    def is_safe(self) -> bool:
        return not self.blockers and not self.recipe_flags


def verify_meal(
    lookups: list[LookupResult],
    recipe_steps: Sequence[str] = (),
    risky_terms: TermMatcher | None = None,
) -> MealVerification:
    """Classify each ingredient reading and optionally scan the recipe for risky mentions.

    Args:
        lookups: One reading per submitted ingredient, in the submitted order.
        recipe_steps: The normalized recipe steps to scan for risky terms.
        risky_terms: The index's worse-than-safe terms, prepared for matching.
            ``None`` skips the recipe scan entirely (the admin edit gate).
    """
    blockers: list[tuple[str, TraceReading]] = []
    unverified: list[str] = []
    for lookup in lookups:
        if lookup.error:
            blockers.append((lookup.ingredient, TraceReading.UNVERIFIABLE))
        elif not _is_rated(lookup):
            # No entry, or an entry the index never rated: unknown, not safe.
            unverified.append(lookup.ingredient)
        else:
            level = _grounded_verdict([lookup])
            if level is not SafetyLevel.SAFE:
                blockers.append((lookup.ingredient, _LEVEL_READING[level]))

    recipe_flags: list[str] = []
    if risky_terms is not None:
        seen: set[str] = set()
        for step in recipe_steps:
            for term in risky_terms.found_in(step):
                if term not in seen:
                    seen.add(term)
                    recipe_flags.append(term)

    return MealVerification(blockers=blockers, unverified=unverified, recipe_flags=recipe_flags)


def _is_rated(lookup: LookupResult) -> bool:
    """True when the index has at least one rated reading for the ingredient.

    A miss returns no candidates, and a row with NULL compatibility surfaces as
    ``unknown``; neither is evidence of safety, so both read as unrated here.
    """
    return any(
        candidate.compatibility != CompatibilityVerdict.UNKNOWN.value
        for candidate in lookup.candidates
    )
