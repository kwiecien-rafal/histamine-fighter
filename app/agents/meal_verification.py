"""The code-owned safety check for a composed meal.

A pure function over the index readings the composer already gathered, so the
gate that decides whether a meal is safe can be unit-tested without a database.
It owns the verdict the same way the dish lookup does: an ``avoid`` reading (or
one that could not be read) blocks; a ``depends`` reading (moderately compatible)
is *cautioned*, kept with the index's own moderation note rather than stripped,
matching the policy the admin edit gate already applies; an ingredient the index
cannot vouch for is *unknown*, not safe, so it passes the automated gate but is
recorded for the admin to clear. When a ``risky_terms`` matcher is supplied (the
composer path), the recipe prose is also scanned for any index-flagged term the
model wrote into the steps but kept off the verified list; the admin edit gate
skips the scan so a human note like "fine in moderation" is not rejected for
naming a term.

"Cannot vouch for" covers two cases the rest of the app keeps apart from safe: a
name with no index entry, and a name the index lists but never rated (a NULL
compatibility). Both are recorded as unverified rather than waved through.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.term_match import TermMatcher
from app.enums import Compatibility, CompatibilityVerdict, SafetyLevel, TraceReading
from app.schemas.meal import CautionedIngredient
from app.services.ingredient_lookup import LookupResult, grounded_verdict

# The index's guidance when a depends-rated row carries no note of its own.
_DEFAULT_MODERATION_NOTE = "use in moderation"


@dataclass(frozen=True, slots=True)
class MealVerification:
    """The outcome of checking a submitted meal against the curated index."""

    blockers: list[tuple[str, TraceReading]]
    cautioned: list[CautionedIngredient]
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
        risky_terms: The index's avoid-level terms, prepared for matching.
            ``None`` skips the recipe scan entirely (the admin edit gate).
    """
    blockers: list[tuple[str, TraceReading]] = []
    cautioned: list[CautionedIngredient] = []
    unverified: list[str] = []
    for lookup in lookups:
        if lookup.error:
            blockers.append((lookup.ingredient, TraceReading.UNVERIFIABLE))
        elif not _is_rated(lookup):
            # No entry, or an entry the index never rated: unknown, not safe.
            unverified.append(lookup.ingredient)
        else:
            level = grounded_verdict([lookup])
            if level is SafetyLevel.AVOID:
                blockers.append((lookup.ingredient, TraceReading.AVOID))
            elif level is SafetyLevel.DEPENDS:
                cautioned.append(
                    CautionedIngredient(name=lookup.ingredient, note=_moderation_note(lookup))
                )

    recipe_flags: list[str] = []
    if risky_terms is not None:
        seen: set[str] = set()
        for step in recipe_steps:
            for term in risky_terms.found_in(step):
                if term not in seen:
                    seen.add(term)
                    recipe_flags.append(term)

    return MealVerification(
        blockers=blockers, cautioned=cautioned, unverified=unverified, recipe_flags=recipe_flags
    )


def _moderation_note(lookup: LookupResult) -> str:
    """The index's own guidance for a depends ingredient, best-informed row first.

    A moderately compatible row's note is the direct answer; when the depends
    verdict comes from mixed rows instead (egg yolk safe, egg white a liberator),
    the risky row's note is the informative one. Never model-written: the model may
    keep the ingredient, only the index says how.
    """
    moderate = Compatibility.MODERATELY_COMPATIBLE.value
    safe = Compatibility.WELL_TOLERATED.value
    for candidate in lookup.candidates:
        if candidate.notes and candidate.compatibility == moderate:
            return candidate.notes
    for candidate in lookup.candidates:
        if candidate.notes and candidate.compatibility != safe:
            return candidate.notes
    return _DEFAULT_MODERATION_NOTE


def _is_rated(lookup: LookupResult) -> bool:
    """True when the index has at least one rated reading for the ingredient.

    A miss returns no candidates, and a row with NULL compatibility surfaces as
    ``unknown``; neither is evidence of safety, so both read as unrated here.
    """
    return any(
        candidate.compatibility != CompatibilityVerdict.UNKNOWN.value
        for candidate in lookup.candidates
    )
