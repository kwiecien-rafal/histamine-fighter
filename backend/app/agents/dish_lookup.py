"""The flagship dish-lookup agent: propose, confirm, assess — and pivot.

The flow is human-in-the-loop. ``propose`` decomposes a dish into a candidate
ingredient list from the model's own culinary knowledge; the user reviews and
edits that list; ``assess`` reads each confirmed ingredient from the curated
index, computes the verdict in code, and has the model write the explanation,
adaptations and advisories that justify it.

Adaptation is dish-first, not ingredient-first. Only avoid-level ingredients
get adaptation entries; depends-level ones get advisory notes, because swapping
a marginal ingredient ruins dishes without making anyone safer. The model
groups same-purpose ingredients (tomato and tomato paste are one tomato base),
tags each group's culinary role, and may answer ``omit`` or ``no_safe_swap``
instead of forcing a swap. From those roles, code derives whether the dish
keeps its identity; when it does not, ``alternatives`` suggests different
dishes, each vetted only by being looked up again through this same flow.

The index is a *risk registry*: it records the ingredients that matter for
histamine intolerance — mostly ones to avoid, plus some noted as well tolerated
— so an ingredient absent from it carries no known risk. The verdict is the
most cautious risk the index records across the confirmed ingredients. The
model never decides it (and culinary roles never feed into it), so the verdict
and the prose can never disagree. Disambiguation is verdict-invariant by
construction: the model may drop a row that does not denote the ingredient, but
a keep-list that would move a resolved level is ignored, so the verdict is the
same with or without it. A swap the index flags is rejected. One it has no
record of ships as a culinary suggestion the user re-vets by looking it up.

User confirmation is what closes the enumeration gap the old tool-calling loop
had: the index is authoritative for *scoring* ingredients, not for
*enumerating* them, and a decomposition the model got wrong is now the user's
to fix rather than silently trusted. One floor remains: a lookup that *errored*
read nothing, so it is not evidence of safety — any errored lookup keeps the
verdict at "depends" or worse.
"""

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, replace
from typing import assert_never

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agents.base import BaseAgent, loggable_messages
from app.agents.prompting import load_prompt, render_prompt, strip_region_tags
from app.enums import (
    AdaptationAction,
    AlternativeGoal,
    Compatibility,
    CulinaryRole,
    DishIntegrity,
    HistamineMechanism,
    SafetyLevel,
)
from app.llm.errors import LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.models import CuratedMeal
from app.schemas.meal import (
    MAX_ADVISORY_CHARS,
    MAX_ALTERNATIVES,
    MAX_CONFIRMED_INGREDIENTS,
    MAX_DISH_CHARS,
    MAX_INGREDIENT_CHARS,
    MAX_PITCH_CHARS,
    MAX_REASON_CHARS,
    Adaptation,
    AdaptationDraft,
    Advisory,
    AdvisoryDraft,
    AlternativeDraft,
    ConfirmedIngredient,
    DisambiguationDraft,
    DishAlternative,
    DishAlternativesDraft,
    DishAlternativesResponse,
    DishAssessmentResponse,
    DishExplanationDraft,
    IngredientAssessment,
    IngredientProposalResponse,
    ProposedIngredient,
    ProposedIngredientDraft,
    ProposedIngredients,
)
from app.services.ingredient_lookup import (
    LookupCandidate,
    LookupResult,
    lookup_ingredients,
)
from app.services.ingredient_service import IngredientMatch, IngredientService
from app.services.meal_service import MealService

log = structlog.get_logger(__name__)

_SUBSTITUTE_LIMIT = 3
# Most well-tolerated anchors to steer the alternatives prompt with — a focused
# set that points a direction without burying the goal in a long ingredient list.
_MAX_SAFE_ANCHORS = 9
_INVOCATION_ERROR = (
    "The language model failed to complete the dish lookup. "
    "If you selected a custom model, make sure it supports structured output."
)
# Stands in for a model reason that justified a rejected swap, and for entries
# the model never covered — never a reason that argues for a replacement.
_NO_SAFE_SWAP_REASON = "No replacement we could verify keeps this dish intact."

# The role an entry takes when the model gave no usable one — a garbled role
# string, or an ingredient it never mentioned at all. Deliberately not CORE:
# only the model's *explicit* "core" may cost a dish its identity (see
# _integrity), so a code-chosen default never flips the result to "find another
# dish" on the strength of the model's forgetfulness alone.
_UNCERTAIN_ROLE = CulinaryRole.SUPPORTING

# Each prompt's full set of region tags. Every user-supplied value is stripped
# against its prompt's whole set, not just its own region, so a value cannot
# forge a sibling region's delimiter to smuggle text into a code-owned section.
_PROPOSE_TAGS = ("dish",)
_SYNTHESIS_TAGS = (
    "dish_text",
    "confirmed_ingredients",
    "verdict",
    "avoid_ingredients",
    "watch_ingredients",
)
_ALTERNATIVES_TAGS = ("dish_text", "excluded_ingredients", "safe_anchors", "already_suggested")
_DISAMBIGUATE_TAGS = ("dish_text", "ingredients")


def _goal_line(goal: AlternativeGoal) -> str:
    """The code-owned prompt line for a goal; the enum value is never interpolated.

    A ``match`` rather than a lookup table, so adding an ``AlternativeGoal`` is a
    type error here until it is handled, never a runtime ``KeyError``.
    """
    match goal:
        case AlternativeGoal.ANY_MEAL:
            return "Suggest any satisfying meals; they need not resemble the original dish."
        case AlternativeGoal.SAME_STYLE:
            return "Suggest dishes in the same style and format as the original."
        case AlternativeGoal.SIMILAR_FLAVOURS:
            return "Suggest dishes with a similar flavour profile, even in a different format."
    assert_never(goal)


# Dish-level severity, worst last, so caution is a simple max.
_SAFETY_SEVERITY: dict[SafetyLevel, int] = {
    SafetyLevel.SAFE: 0,
    SafetyLevel.DEPENDS: 1,
    SafetyLevel.AVOID: 2,
}

_COMPATIBILITY_SAFETY: dict[Compatibility, SafetyLevel] = {
    Compatibility.WELL_TOLERATED: SafetyLevel.SAFE,
    Compatibility.MODERATELY_COMPATIBLE: SafetyLevel.DEPENDS,
    Compatibility.INCOMPATIBLE: SafetyLevel.AVOID,
    Compatibility.POORLY_TOLERATED: SafetyLevel.AVOID,
}


@dataclass(frozen=True, slots=True)
class FlaggedIngredient:
    """One risky ingredient summarised for synthesis, errored or normal.

    One shape serves both an errored watch entry (read nothing, so it joins the
    watch tier unverified) and a normal flagged entry; an errored one leaves the
    optional fields at their defaults. ``severity`` is the resolved per-ingredient
    risk and decides the entry's tier; only avoid-level entries carry
    ``safe_options``.
    """

    ingredient: str
    severity: SafetyLevel
    error: bool = False
    compatibility: str | None = None
    ambiguous: bool = False
    readings: tuple[tuple[str, str], ...] = ()
    mechanisms: tuple[HistamineMechanism, ...] = ()
    category: str | None = None
    matched_on: str | None = None
    matched_as: str | None = None
    safe_options: tuple[str, ...] = ()


def _more_cautious(first: SafetyLevel, second: SafetyLevel) -> SafetyLevel:
    return first if _SAFETY_SEVERITY[first] >= _SAFETY_SEVERITY[second] else second


def _resolve_levels(levels: set[SafetyLevel]) -> SafetyLevel:
    """Resolve one ingredient's risk from the levels its index matches map to.

    Disagreement is resolved at the *safety* layer, not the raw compatibility
    one, so two distinct compatibilities that mean the same thing never look like
    a conflict:

    - no levels (ingredient absent or unrated) -> safe;
    - all matches agree -> that level (two ``avoid`` readings stay ``avoid``, two
      ``safe`` readings stay ``safe``);
    - a ``safe`` reading coexists with a risky one -> ``depends`` (the genuine
      egg-yolk-vs-egg-white case: it depends which form the dish uses);
    - every reading is risky but they differ in degree -> the most cautious of
      them (caution is never softened to ``depends``).
    """
    if not levels:
        return SafetyLevel.SAFE
    floor = min(levels, key=_SAFETY_SEVERITY.__getitem__)
    ceil = max(levels, key=_SAFETY_SEVERITY.__getitem__)
    if floor is ceil:
        return ceil
    return SafetyLevel.DEPENDS if floor is SafetyLevel.SAFE else ceil


def _compatibility_safety(value: str) -> SafetyLevel:
    """Map one lookup-result compatibility string to risk (``unknown`` -> safe)."""
    try:
        return _COMPATIBILITY_SAFETY[Compatibility(value)]
    except ValueError:
        return SafetyLevel.SAFE


def _candidates_safety(candidates: list[LookupCandidate]) -> SafetyLevel:
    """The risk one lookup's candidate rows contribute to the dish."""
    return _resolve_levels({_compatibility_safety(c.compatibility) for c in candidates})


def _matches_safety(matches: list[IngredientMatch]) -> SafetyLevel:
    """The risk a set of index matches implies, used to vet a proposed swap."""
    return _resolve_levels(
        {
            _COMPATIBILITY_SAFETY[match.ingredient.compatibility]
            for match in matches
            if match.ingredient.compatibility is not None
        }
    )


def _worst_risky(candidates: list[LookupCandidate]) -> LookupCandidate | None:
    """One lookup's most severe risky candidate, or ``None`` when nothing is risky."""
    risky = [
        candidate
        for candidate in candidates
        if _compatibility_safety(candidate.compatibility) is not SafetyLevel.SAFE
    ]
    if not risky:
        return None
    return max(risky, key=lambda c: _SAFETY_SEVERITY[_compatibility_safety(c.compatibility)])


def _format_flagged(flagged: list[FlaggedIngredient]) -> str:
    """The flagged ingredients as labelled lines for the synthesis prompt.

    Prose-shaped rather than JSON: the model grounds its explanation in named
    facts it can quote ("mechanisms: high histamine") instead of decoding
    key/value structure, and the synthesis system prompt describes these labels
    directly. Serves both severity sections: only avoid-level entries ever carry
    ``safe_options``, so the candidate-swaps line never appears for watch lines.

    An errored lookup renders as a single unverified line. An ambiguous one
    lists every index reading instead of one compatibility, so the section
    label ("depends-level") and the line can never contradict each other.
    """
    if not flagged:
        return "None."
    lines: list[str] = []
    for entry in flagged:
        if entry.error:
            lines.append(
                f"- {entry.ingredient} — could not be read from the index; treat as unknown."
            )
            continue
        if entry.ambiguous:
            readings = ", ".join(f"{name} ({level})" for name, level in entry.readings)
            parts = [f"{entry.ingredient} — conflicting readings: {readings}"]
        else:
            parts = [f"{entry.ingredient} — {entry.compatibility}"]
        if entry.category:
            parts.append(f"category: {entry.category}")
        if entry.matched_on == "category":
            parts.append(f'flagged as a member of the indexed group "{entry.matched_as}"')
        if entry.mechanisms:
            parts.append("mechanisms: " + ", ".join(entry.mechanisms))
        if entry.safe_options:
            parts.append("candidate swaps: " + ", ".join(entry.safe_options))
        lines.append(f"- {'; '.join(parts)}.")
    return "\n".join(lines)


def _format_candidates(lookups: list[LookupResult]) -> str:
    """The ambiguous lookups as labelled lines for the disambiguation prompt.

    One line per ingredient with the rows it matched, each shown with its food
    category so the model can judge identity. Compatibility is withheld on
    purpose: the model resolves which row an ingredient is, never how risky.
    """
    lines: list[str] = []
    for lookup in lookups:
        rows = ", ".join(
            f"{candidate.name} ({candidate.category})" if candidate.category else candidate.name
            for candidate in lookup.candidates
        )
        lines.append(f"- {lookup.ingredient}: {rows}")
    return "\n".join(lines)


def _grounded_verdict(lookups: list[LookupResult]) -> SafetyLevel:
    """The dish verdict the index supports: the most cautious per-ingredient risk.

    Each lookup contributes one level; ingredients absent from the index return no
    candidates and add no risk. With nothing risky recorded the verdict is safe.
    """
    verdict = SafetyLevel.SAFE
    for lookup in lookups:
        verdict = _more_cautious(verdict, _candidates_safety(lookup.candidates))
    return verdict


def _clipped(value: str, limit: int = MAX_INGREDIENT_CHARS) -> str:
    return value.strip()[:limit].rstrip()


def _normalized(items: list[ProposedIngredientDraft]) -> list[ProposedIngredient]:
    """Degrade the model's draft items into valid response items.

    The draft schema is deliberately unconstrained so a sloppy model cannot fail
    the parse; everything the response schema enforces is normalized here
    instead — trim and truncate each field, drop blanks, dedupe
    (case-insensitive, order kept), cap the count.
    """
    kept: list[ProposedIngredient] = []
    seen: set[str] = set()
    for item in items:
        name = _clipped(item.name)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        category = _clipped(item.category) if item.category else ""
        kept.append(ProposedIngredient(name=name, category=category or None))
        if len(kept) == MAX_CONFIRMED_INGREDIENTS:
            break
    return kept


def _ingredient_assessment(name: str, lookup: LookupResult) -> IngredientAssessment:
    """One confirmed ingredient's reading for the per-ingredient badge.

    A failed lookup is marked ``error`` and reads as "depends": a cautious
    default consistent with the floored dish verdict, not an index reading.
    """
    if lookup.error:
        return IngredientAssessment(name=name, safety=SafetyLevel.DEPENDS, found=False, error=True)
    worst = _worst_risky(lookup.candidates)
    return IngredientAssessment(
        name=name,
        safety=_candidates_safety(lookup.candidates),
        found=lookup.found,
        matched_on=lookup.matched_on,
        mechanisms=list(worst.mechanisms) if worst else [],
    )


def _parse_role(value: str) -> CulinaryRole | None:
    """Parse a role, or ``None`` when the model wrote something off-enum.

    The caller picks the fallback (and records that it had to), so the choice
    and its logging live in one place rather than being hidden here.
    """
    try:
        return CulinaryRole(value.strip().lower())
    except ValueError:
        return None


def _parse_action(value: str) -> AdaptationAction | None:
    """Parse an action, or ``None`` when the model wrote something off-enum."""
    try:
        return AdaptationAction(value.strip().lower())
    except ValueError:
        return None


def _default_reason(action: AdaptationAction, swap: str) -> str:
    """A neutral reason for an entry the model left blank."""
    if action is AdaptationAction.SWAP:
        return f"{swap} is a well-tolerated stand-in here."
    if action is AdaptationAction.OMIT:
        return "The dish holds up without it."
    return _NO_SAFE_SWAP_REASON


def _normalized_adaptations(
    drafts: list[AdaptationDraft], avoid_names: dict[str, str]
) -> list[Adaptation]:
    """Degrade the model's adaptation drafts into valid entries.

    ``avoid_names`` maps casefolded avoid-level names to their confirmed
    spelling — the only ingredients an adaptation may cover. A draft pulling in
    a depends-level or invented name is exactly the over-swapping this design
    kills, so such names are filtered out and an emptied entry is dropped.
    Overlapping entries resolve first-wins; unknown enum strings, a missing
    swap, and over-long reasons all degrade in code, never fail the parse.

    The guiding rule on ``reason``: the model's words survive only while its
    stated action does. Whenever code has to infer or demote the action down to
    ``no_safe_swap``, the reason — which argued for some replacement — is reset
    to a neutral template, never carried onto a card that now says the opposite.
    """
    kept: list[Adaptation] = []
    covered: set[str] = set()
    for draft in drafts:
        names: list[str] = []
        for raw in draft.ingredients:
            key = _clipped(raw).casefold()
            confirmed = avoid_names.get(key)
            if confirmed is None or key in covered:
                continue
            covered.add(key)
            names.append(confirmed)
        if not names:
            continue

        swap = _clipped(draft.swap) if draft.swap else ""
        parsed_role = _parse_role(draft.role)
        parsed_action = _parse_action(draft.action)
        reason = _clipped(draft.reason, MAX_REASON_CHARS)

        # A missing role degrades to the uncertain default, not core: only an
        # explicit "core" from the model may later cost the dish its identity.
        role = parsed_role if parsed_role is not None else _UNCERTAIN_ROLE

        if parsed_action is None:
            # Off-enum action: trust a named swap (still vetted downstream),
            # otherwise fall to no_safe_swap and drop the now-misleading reason.
            action = AdaptationAction.SWAP if swap else AdaptationAction.NO_SAFE_SWAP
            if action is AdaptationAction.NO_SAFE_SWAP:
                reason = ""
        elif parsed_action is AdaptationAction.SWAP and not swap:
            # A swap action naming no swap often hides the replacement in its
            # reason instead; that name never passed the index check, so the
            # reason resets along with the action.
            action = AdaptationAction.NO_SAFE_SWAP
            reason = ""
        else:
            action = parsed_action

        if action is not AdaptationAction.SWAP:
            swap = ""

        if parsed_role is None or parsed_action is None:
            # Counts/booleans only, no user content: a sloppy draft is a model-
            # quality signal, and an action fallback under an explicit "core"
            # role is the one fallback path that can still reach integrity=lost.
            log.info(
                "dish_lookup.draft_degraded",
                role_fallback=parsed_role is None,
                action_fallback=parsed_action is None,
            )
        elif (
            parsed_action is AdaptationAction.NO_SAFE_SWAP and parsed_role is not CulinaryRole.CORE
        ):
            # The model itself called a no-safe-swap ingredient less than core:
            # the dish can't be fixed yet supposedly keeps its identity. Legal
            # (integrity stays core-only), but a contradiction worth seeing.
            log.info("dish_lookup.role_action_conflict", role=role.value)

        kept.append(
            Adaptation(
                ingredients=names,
                role=role,
                action=action,
                swap=swap or None,
                reason=reason or _default_reason(action, swap),
            )
        )
    return kept


def _default_advisory(entry: FlaggedIngredient) -> str:
    """A templated note from the index's own facts when the model wrote none."""
    if entry.error:
        return "We couldn't check this one against the index — treat it as unknown for now."
    mechanisms = [str(mechanism).replace("_", " ") for mechanism in entry.mechanisms]
    if mechanisms:
        return f"Tolerance varies — flagged for: {', '.join(mechanisms)}."
    return "Tolerance varies from person to person."


def _normalized_advisories(
    drafts: list[AdvisoryDraft], watch_flagged: list[FlaggedIngredient]
) -> list[Advisory]:
    """One advisory per depends-level ingredient, model prose preferred.

    Drafts naming anything outside the watch list are dropped; a watch
    ingredient the model skipped still gets a note templated from its index
    mechanisms, so every flagged ingredient is visibly addressed.
    """
    by_key = {entry.ingredient.casefold(): entry for entry in watch_flagged}
    notes: dict[str, str] = {}
    for draft in drafts:
        key = _clipped(draft.ingredient).casefold()
        if key in by_key and key not in notes:
            notes[key] = _clipped(draft.note, MAX_ADVISORY_CHARS)
    return [
        Advisory(
            ingredient=entry.ingredient,
            note=notes.get(key) or _default_advisory(entry),
        )
        for key, entry in by_key.items()
    ]


def _verified_alternatives(meals: list[CuratedMeal]) -> list[DishAlternative]:
    """Approved-pool meals as verified suggestions; the description is the pitch.

    The claim is sound because membership means code-verified plus admin-approved.
    A meal whose name clips to blank is skipped; dedupe and the cap happen when the
    tiers are combined, where verified picks fill first and so keep precedence over
    generated ones.
    """
    kept: list[DishAlternative] = []
    for meal in meals:
        name = _clipped(meal.name, MAX_DISH_CHARS)
        if not name:
            continue
        kept.append(
            DishAlternative(
                name=name, pitch=_clipped(meal.description, MAX_PITCH_CHARS), source="verified"
            )
        )
    return kept


def _generated_alternatives(items: list[AlternativeDraft]) -> list[DishAlternative]:
    """The model's fresh ideas, clipped; blanks dropped. Makes no safety claim, so
    each is re-vetted when the user looks it up. Dedupe and the cap happen when the
    tiers are combined."""
    kept: list[DishAlternative] = []
    for item in items:
        name = _clipped(item.name, MAX_DISH_CHARS)
        if not name:
            continue
        kept.append(
            DishAlternative(
                name=name, pitch=_clipped(item.pitch, MAX_PITCH_CHARS), source="generated"
            )
        )
    return kept


def _take_alternatives(
    kept: list[DishAlternative], seen: set[str], items: Iterable[DishAlternative]
) -> None:
    """Append items new by casefolded name into ``kept``, up to MAX_ALTERNATIVES.

    ``kept`` and ``seen`` are shared across the verified then generated passes, so
    one cap and one dedupe span both tiers: a generated idea repeating a verified
    name (or the dish, pre-seeded in ``seen``) cannot take a slot, and verified
    picks keep precedence because they fill first. An empty result is a valid
    "nothing fits" answer.
    """
    for item in items:
        if len(kept) == MAX_ALTERNATIVES:
            return
        key = item.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)


def _integrity(adaptations: list[Adaptation]) -> DishIntegrity:
    """Grade what the adaptations do to the dish's identity.

    A group only reaches ``core`` from the model stating it (see
    ``_UNCERTAIN_ROLE``), so this most consequential signal never rests on a
    code-chosen default. ``lost`` is a core group with no safe swap, the dead end
    that tells the user to abandon the dish. ``altered`` is a core group that was
    swapped or omitted: the dish is still makeable but no longer quite itself, so
    the pivot is offered without the abandon-it wording. One swapped core
    ingredient is enough, since a dish can hinge on several.
    """
    if any(
        entry.role is CulinaryRole.CORE and entry.action is AdaptationAction.NO_SAFE_SWAP
        for entry in adaptations
    ):
        return DishIntegrity.LOST
    if any(entry.role is CulinaryRole.CORE for entry in adaptations):
        return DishIntegrity.ALTERED
    return DishIntegrity.PRESERVED


class DishLookupAgent(BaseAgent):
    """Classifies a dish by grounding the verdict in curated ingredient data."""

    _invocation_error = _INVOCATION_ERROR

    def __init__(
        self, chat: ChatModel, service: IngredientService, meal_service: MealService
    ) -> None:
        super().__init__(chat)
        self._service = service
        self._meal_service = meal_service
        self._propose_prompt = render_prompt(
            load_prompt("dish_lookup/propose_system"),
            "dish_lookup/propose_system",
            input_tag="<dish>",
        )
        self._propose_user_template = load_prompt("dish_lookup/propose_user")
        self._synthesis_prompt = render_prompt(
            load_prompt("dish_lookup/synthesis_system"),
            "dish_lookup/synthesis_system",
            input_tag="<dish_text>",
        )
        self._synthesis_user_template = load_prompt("dish_lookup/synthesis_user")
        self._alternatives_prompt = render_prompt(
            load_prompt("dish_lookup/alternatives_system"),
            "dish_lookup/alternatives_system",
            input_tag="<dish_text>",
        )
        self._alternatives_user_template = load_prompt("dish_lookup/alternatives_user")
        self._disambiguate_prompt = render_prompt(
            load_prompt("dish_lookup/disambiguate_system"),
            "dish_lookup/disambiguate_system",
            input_tag="<dish_text>",
        )
        self._disambiguate_user_template = load_prompt("dish_lookup/disambiguate_user")

    def stream(self, dish: str) -> AsyncIterator[str]:
        # Declared, not omitted, so the streaming contract stays explicit; deferred.
        raise NotImplementedError("Streaming dish lookup is not implemented yet.")

    async def propose(self, dish: str) -> IngredientProposalResponse:
        """Decompose the dish into the ingredient list the user will confirm."""
        self._begin_usage()
        messages: list[BaseMessage] = [
            SystemMessage(self._propose_prompt),
            HumanMessage(
                render_prompt(
                    self._propose_user_template,
                    "dish_lookup/propose_user",
                    dish=strip_region_tags(dish, _PROPOSE_TAGS),
                )
            ),
        ]
        log.debug("dish_lookup.propose_request", messages=loggable_messages(messages))
        proposal = await self._structured_invoke(ProposedIngredients, messages, step="propose")
        log.debug("dish_lookup.propose_reply", proposal=proposal.model_dump())
        ingredients = _normalized(proposal.ingredients)
        # Counts only, never names: this always-on line carries no user content.
        log.info(
            "dish_lookup.proposed",
            proposed=len(proposal.ingredients),
            kept=len(ingredients),
            model=self._chat.model_name,
        )
        return IngredientProposalResponse(
            dish=dish,
            ingredients=ingredients,
            model=self._chat.model_name,
            usage=self._collect_usage(),
        )

    async def assess(
        self, dish: str, ingredients: list[ConfirmedIngredient]
    ) -> DishAssessmentResponse:
        """Read each confirmed ingredient from the index and assemble the answer."""
        self._begin_usage()
        lookups = await lookup_ingredients(
            self._service, [(item.name, item.category) for item in ingredients]
        )
        log.debug(
            "dish_lookup.lookups",
            results=[
                {
                    "ingredient": lookup.ingredient,
                    "found": lookup.found,
                    "matched_on": lookup.matched_on,
                    "candidates": [(c.name, c.compatibility) for c in lookup.candidates],
                }
                for lookup in lookups
            ],
        )

        # The matcher cannot weigh the dish, so an ambiguous name may carry a
        # clearly wrong row. Let the model drop those before anything reads the
        # candidates. Best effort: a failure leaves the lookups untouched.
        lookups = await self._disambiguate(dish, lookups)

        # A failed lookup (DB blip) read nothing, so it is not evidence of safety:
        # the confirmed list is complete by declaration, but its grounding is not.
        grounded = [lookup for lookup in lookups if not lookup.error]
        verdict = _grounded_verdict(grounded)
        if len(grounded) < len(lookups):
            verdict = _more_cautious(verdict, SafetyLevel.DEPENDS)
            log.warning(
                "dish_lookup.incomplete_grounding",
                grounded=len(grounded),
                confirmed=len(lookups),
                verdict=verdict.value,
            )

        assessments = [
            _ingredient_assessment(item.name, lookup)
            for item, lookup in zip(ingredients, lookups, strict=True)
        ]
        flagged = self._flagged(lookups)
        # Severity decides the tier: avoid-level ingredients are adaptation
        # material, depends-level ones only warrant a note. _candidates_safety
        # is the same reading the per-ingredient badges use, so the egg-style
        # ambiguous case (safe + risky readings) lands in advisories.
        avoid_flagged = [entry for entry in flagged if entry.severity is SafetyLevel.AVOID]
        watch_flagged = [entry for entry in flagged if entry.severity is SafetyLevel.DEPENDS]
        avoid_flagged = await self._attach_safe_options(avoid_flagged)
        draft = await self._synthesize(dish, ingredients, verdict, avoid_flagged, watch_flagged)
        avoid_names = {entry.ingredient.casefold(): entry.ingredient for entry in avoid_flagged}
        adaptations = await self._ground_adaptations(
            verdict, _normalized_adaptations(draft.adaptations, avoid_names), avoid_names
        )
        advisories = _normalized_advisories(draft.advisories, watch_flagged)
        integrity = _integrity(adaptations)
        # This always-on line stays clear of user-typed strings: checked and
        # unverified are counts, and drivers names the curated index rows that
        # matched (matched_as), not the user's spellings. dish is the model's
        # cleaned name — derived from user input, and the only such field here.
        log.info(
            "dish_lookup.verdict",
            dish=draft.dish,
            verdict=verdict.value,
            integrity=integrity.value,
            checked=len(lookups),
            drivers=[entry.matched_as for entry in flagged if not entry.error],
            unverified=sum(1 for entry in flagged if entry.error),
            adaptations=len(adaptations),
            advisories=len(advisories),
            model=self._chat.model_name,
        )
        return DishAssessmentResponse(
            dish=draft.dish,
            verdict=verdict,
            explanation=draft.explanation,
            adaptations=adaptations,
            advisories=advisories,
            integrity=integrity,
            ingredients=assessments,
            model=self._chat.model_name,
            usage=self._collect_usage(),
        )

    async def _disambiguate(self, dish: str, lookups: list[LookupResult]) -> list[LookupResult]:
        """Drop clearly wrong rows from the ambiguous lookups, verdict invariant.

        Only ambiguous lookups (candidates that disagree on compatibility) are
        eligible, since nothing else can change the verdict, and if none are the
        call is skipped. One batched call returns, per ingredient, the rows to
        keep. Safety stays code's alone: a row name the model invents is ignored,
        an empty keep-list leaves every original in place, any failure leaves the
        lookups untouched, and a keep-list that would move an ingredient's
        resolved level is held — the model reaching for the verdict, not identity.
        So a prune only ever cleans the prose and adaptations, never the verdict,
        which is provably identical with or without this step.
        """
        eligible = [lookup for lookup in lookups if lookup.found and lookup.ambiguous]
        if not eligible:
            return lookups

        messages: list[BaseMessage] = [
            SystemMessage(self._disambiguate_prompt),
            HumanMessage(
                render_prompt(
                    self._disambiguate_user_template,
                    "dish_lookup/disambiguate_user",
                    dish=strip_region_tags(dish, _DISAMBIGUATE_TAGS),
                    ingredients=strip_region_tags(_format_candidates(eligible), _DISAMBIGUATE_TAGS),
                )
            ),
        ]
        log.debug("dish_lookup.disambiguation_request", messages=loggable_messages(messages))
        try:
            draft = await self._structured_invoke(
                DisambiguationDraft, messages, step="disambiguate"
            )
        except LLMInvocationError:
            log.warning("dish_lookup.disambiguation_failed", eligible=len(eligible))
            return lookups
        log.debug("dish_lookup.disambiguation_reply", draft=draft.model_dump())

        keep_by_ingredient = {
            reading.ingredient.strip().casefold(): {
                name.strip().casefold() for name in reading.keep if name.strip()
            }
            for reading in draft.readings
        }
        dropped = 0
        held = 0
        revised: list[LookupResult] = []
        for lookup in lookups:
            keep = keep_by_ingredient.get(lookup.ingredient.casefold())
            if not (lookup.found and lookup.ambiguous) or not keep:
                revised.append(lookup)
                continue
            kept = [c for c in lookup.candidates if c.name.casefold() in keep]
            if not kept or len(kept) == len(lookup.candidates):
                revised.append(lookup)
                continue
            if _candidates_safety(kept) is not _candidates_safety(lookup.candidates):
                held += 1
                revised.append(lookup)
                continue
            dropped += len(lookup.candidates) - len(kept)
            revised.append(
                replace(
                    lookup,
                    candidates=kept,
                    ambiguous=len({c.compatibility for c in kept}) > 1,
                )
            )

        # Counts and the model only: this always-on line carries no user content.
        log.info(
            "dish_lookup.disambiguated",
            eligible=len(eligible),
            dropped=dropped,
            held=held,
            model=self._chat.model_name,
        )
        return revised

    def _flagged(self, lookups: list[LookupResult]) -> list[FlaggedIngredient]:
        """Summarise the risky ingredients for the synthesis step.

        One entry per risky lookup, built from its most severe risky candidate, so
        the model writes adaptations and explanations for exactly what the index
        flagged. ``severity`` is the resolved per-ingredient risk — the same
        reading the per-ingredient badge shows — and decides the entry's tier.

        Two special entry shapes keep the prompt honest: an *errored* lookup read
        nothing, so it joins the watch tier marked unverified (matching its
        floored badge) rather than silently vanishing from the prompt; and an
        *ambiguous* lookup carries all of its index readings, so the model can
        explain a conflict instead of guessing at one reading shown out of
        context.
        """
        flagged: list[FlaggedIngredient] = []
        for lookup in lookups:
            if lookup.error:
                flagged.append(
                    FlaggedIngredient(
                        ingredient=lookup.ingredient, severity=SafetyLevel.DEPENDS, error=True
                    )
                )
                continue
            worst = _worst_risky(lookup.candidates)
            if worst is None:
                continue
            flagged.append(
                FlaggedIngredient(
                    ingredient=lookup.ingredient,
                    severity=_candidates_safety(lookup.candidates),
                    compatibility=worst.compatibility,
                    ambiguous=lookup.ambiguous,
                    readings=tuple((c.name, c.compatibility) for c in lookup.candidates),
                    mechanisms=worst.mechanisms,
                    category=worst.category,
                    # How the index flagged it: a category-matched ingredient was
                    # caught as a member of the group in matched_as ("Hard Cheese"),
                    # and the synthesis step phrases it that way.
                    matched_on=lookup.matched_on,
                    matched_as=worst.name,
                )
            )
        return flagged

    async def _attach_safe_options(
        self, flagged: list[FlaggedIngredient]
    ) -> list[FlaggedIngredient]:
        """Return the entries with ``safe_options`` filled from the index by category."""
        attached: list[FlaggedIngredient] = []
        for entry in flagged:
            if not entry.category:
                attached.append(entry)
                continue
            substitutes = await self._service.find_substitutes(
                entry.category, limit=_SUBSTITUTE_LIMIT
            )
            name = entry.ingredient.casefold()
            options = tuple(sub.name for sub in substitutes if sub.name.casefold() != name)
            attached.append(replace(entry, safe_options=options))
        return attached

    async def _synthesize(
        self,
        dish: str,
        ingredients: list[ConfirmedIngredient],
        verdict: SafetyLevel,
        avoid_flagged: list[FlaggedIngredient],
        watch_flagged: list[FlaggedIngredient],
    ) -> DishExplanationDraft:
        messages: list[BaseMessage] = [
            SystemMessage(self._synthesis_prompt),
            HumanMessage(
                render_prompt(
                    self._synthesis_user_template,
                    "dish_lookup/synthesis_user",
                    # The dish text and every ingredient name are direct user
                    # input; none may close its own region or forge a sibling's.
                    dish=strip_region_tags(dish, _SYNTHESIS_TAGS),
                    ingredients=strip_region_tags(
                        ", ".join(item.name for item in ingredients), _SYNTHESIS_TAGS
                    ),
                    avoid_flagged=strip_region_tags(
                        _format_flagged(avoid_flagged), _SYNTHESIS_TAGS
                    ),
                    watch_flagged=strip_region_tags(
                        _format_flagged(watch_flagged), _SYNTHESIS_TAGS
                    ),
                    verdict=verdict.value,
                )
            ),
        ]
        log.debug("dish_lookup.synthesis_request", messages=loggable_messages(messages))
        draft = await self._structured_invoke(DishExplanationDraft, messages, step="synthesize")
        log.debug("dish_lookup.synthesis_reply", draft=draft.model_dump())
        return draft

    async def _ground_adaptations(
        self,
        verdict: SafetyLevel,
        adaptations: list[Adaptation],
        avoid_names: dict[str, str],
    ) -> list[Adaptation]:
        """Vet every proposed swap against the index; never invent one.

        A safe verdict carries no adaptations. A swap the index flags demotes
        its entry to ``no_safe_swap`` in place — the model's reason argued for
        the rejected swap, so a neutral one replaces it. An avoid-level
        ingredient the model never covered gets an appended ``no_safe_swap``
        entry so the gap is still surfaced (and the alternatives pivot still
        offered); its role is the uncertain default, not ``core``, because a
        forgotten ingredient is no evidence the dish is unsalvageable.
        """
        if verdict is SafetyLevel.SAFE:
            return []

        grounded: list[Adaptation] = []
        for entry in adaptations:
            if entry.action is AdaptationAction.SWAP and not await self._swap_is_safe(
                entry.swap or ""
            ):
                log.warning(
                    "dish_lookup.swap_rejected", ingredients=entry.ingredients, swap=entry.swap
                )
                entry = Adaptation(
                    ingredients=entry.ingredients,
                    role=entry.role,
                    action=AdaptationAction.NO_SAFE_SWAP,
                    swap=None,
                    reason=_NO_SAFE_SWAP_REASON,
                )
            grounded.append(entry)

        covered = {name.casefold() for entry in grounded for name in entry.ingredients}
        for key, name in avoid_names.items():
            if key in covered:
                continue
            # name is the user's spelling of an ingredient the index flagged
            # avoid-level, so this warning names a curated-matched ingredient.
            log.warning("dish_lookup.adaptation_missing", ingredient=name)
            grounded.append(
                Adaptation(
                    ingredients=[name],
                    role=_UNCERTAIN_ROLE,
                    action=AdaptationAction.NO_SAFE_SWAP,
                    swap=None,
                    reason=_NO_SAFE_SWAP_REASON,
                )
            )
        return grounded

    async def _swap_is_safe(self, swap: str) -> bool:
        """A swap is usable only if the index does not record a concern for it."""
        matches = await self._service.find_candidates(swap)
        return _matches_safety(matches) is SafetyLevel.SAFE

    async def _safe_anchors(
        self, avoid_ingredients: list[str], prefer_ingredients: list[str]
    ) -> list[str]:
        """Well-tolerated ingredients to steer the suggestions toward.

        The dish's own confirmed-safe ingredients lead: they are the truest "this
        dish, minus the problem" signal. They are then topped up with well-tolerated
        swaps from each excluded ingredient's index category, so a dish with few safe
        parts still gets direction. All curated reads, no model call. An excluded
        ingredient never anchors its own replacement; the result is deduped
        (case-insensitive) and capped.
        """
        excluded = {name.casefold() for name in avoid_ingredients}
        seen: set[str] = set()
        anchors: list[str] = []

        def take(names: Iterable[str]) -> bool:
            """Add names up to the cap; return True once the cap is reached."""
            for name in names:
                key = name.casefold()
                if key in excluded or key in seen:
                    continue
                seen.add(key)
                anchors.append(name)
                if len(anchors) == _MAX_SAFE_ANCHORS:
                    return True
            return False

        # Category swaps are queried only when the dish's safe parts did not fill
        # the cap, so a well-anchored dish does no extra DB work.
        if not take(prefer_ingredients):
            for category in await self._avoid_categories(avoid_ingredients):
                substitutes = await self._service.find_substitutes(
                    category, limit=_SUBSTITUTE_LIMIT
                )
                if take(sub.name for sub in substitutes):
                    break

        log.debug(
            "dish_lookup.safe_anchors",
            avoid=len(avoid_ingredients),
            preferred=len(prefer_ingredients),
            anchors=len(anchors),
        )
        return anchors

    async def _avoid_categories(self, avoid_ingredients: list[str]) -> list[str]:
        """Each excluded ingredient's index category, best match only, deduped.

        Only the top-ranked candidate's category is taken, mirroring how the assess
        path resolves a single reading: a lower fuzzy candidate would drag in a
        category the ingredient does not really belong to.
        """
        matches_by_name = await self._service.find_candidates_many(avoid_ingredients)
        categories: list[str] = []
        seen: set[str] = set()
        for name in avoid_ingredients:
            matches = matches_by_name[name]
            if not matches:
                continue
            category = matches[0].ingredient.category
            if category and category.casefold() not in seen:
                seen.add(category.casefold())
                categories.append(category)
        return categories

    async def alternatives(
        self,
        dish: str,
        goal: AlternativeGoal,
        avoid_ingredients: list[str],
        prefer_ingredients: list[str] | None = None,
    ) -> DishAlternativesResponse:
        """Suggest different dishes once this one cannot keep its identity.

        Two tiers. First retrieve from the verified pool and re-grade each pick
        against the live index. Membership means the meal was code-verified and
        admin-approved, but the index is mutable, so the ``verified`` signal must
        mean safe now, not safe when it was approved. A pick that still grounds to
        safe keeps the signal, with similarity there being pure relevance and the
        *goal* selecting the query axis. One that no longer does drops out, and the
        generation tier fills its place. Then, only if the pool did not fill the
        count, generate fresh ideas to top it up: these make no safety claim and
        are re-vetted when the user looks them up (propose → confirm → assess).
        Both ingredient lists are client-asserted and never touch a verdict:
        ``avoid_ingredients`` exclude pool dishes built on them, while
        ``prefer_ingredients`` (the looked-up dish's own safe parts) lead the
        anchors so suggestions build on what already worked before falling back to
        category swaps.

        Re-grading and retrieval are both zero model calls, so usage is still
        tallied only when the generation tier runs. A picked suggestion is always
        looked up again by name, so a verified pick can read differently when its
        name decomposes into other ingredients than the pool stored. That fresh
        lookup is the intended vetting, not a verdict this pivot owns.
        """
        self._begin_usage()
        prefer = prefer_ingredients or []
        # similar_flavours queries the pool by the safe anchors, so they are needed
        # before retrieval. The other goals only use them to steer generation, so
        # defer that DB work and skip it entirely when the pool fills the count.
        anchors = (
            await self._safe_anchors(avoid_ingredients, prefer)
            if goal is AlternativeGoal.SIMILAR_FLAVOURS
            else []
        )
        picks = await self._verified_picks(goal, dish, anchors, avoid_ingredients)
        verified = _verified_alternatives(await self._still_safe(picks))

        # Fill verified first, deduped against the dish and each other, then gate on
        # the kept count: two pool meals sharing a name (or one echoing the dish)
        # collapse to one slot here, so generation still runs to top the list up
        # rather than the response silently coming back short.
        seen = {dish.strip().casefold()}
        suggestions: list[DishAlternative] = []
        _take_alternatives(suggestions, seen, verified)
        generated: list[DishAlternative] = []
        if len(suggestions) < MAX_ALTERNATIVES:
            if goal is not AlternativeGoal.SIMILAR_FLAVOURS:
                anchors = await self._safe_anchors(avoid_ingredients, prefer)
            already_chosen = [pick.name for pick in suggestions]
            generated = await self._generate_alternatives(
                dish,
                goal,
                anchors,
                avoid_ingredients,
                already_chosen,
                MAX_ALTERNATIVES - len(suggestions),
            )
            _take_alternatives(suggestions, seen, generated)
        # Counts and the goal enum only: this always-on line carries no user content.
        log.info(
            "dish_lookup.alternatives",
            goal=goal.value,
            verified=len(verified),
            generated=len(generated),
            kept=len(suggestions),
            model=self._chat.model_name,
        )
        return DishAlternativesResponse(
            dish=dish,
            goal=goal,
            alternatives=suggestions,
            model=self._chat.model_name,
            usage=self._collect_usage(),
        )

    async def _verified_picks(
        self, goal: AlternativeGoal, dish: str, anchors: list[str], avoid_ingredients: list[str]
    ) -> list[CuratedMeal]:
        """Retrieve approved-pool meals for the goal; the goal picks the query axis.

        ``same_style`` searches by the rejected dish name (the nearest pool dishes
        are the same style, now safe by membership); ``similar_flavours`` searches
        by the safe-anchor flavour terms; ``any_meal`` skips similarity and samples
        at random. The ``avoid_ingredients`` are excluded throughout, so a pool dish
        built on what the user is avoiding is never offered back.
        """
        match goal:
            case AlternativeGoal.SAME_STYLE:
                matches = await self._meal_service.search(
                    dish, k=MAX_ALTERNATIVES, exclude=avoid_ingredients
                )
                return [match.meal for match in matches]
            case AlternativeGoal.SIMILAR_FLAVOURS:
                # No anchors means no flavour query, so search returns nothing and
                # the generation tier fills in.
                matches = await self._meal_service.search(
                    " ".join(anchors), k=MAX_ALTERNATIVES, exclude=avoid_ingredients
                )
                return [match.meal for match in matches]
            case AlternativeGoal.ANY_MEAL:
                return await self._meal_service.random_sample(
                    k=MAX_ALTERNATIVES, exclude=avoid_ingredients
                )
        assert_never(goal)

    async def _still_safe(self, meals: list[CuratedMeal]) -> list[CuratedMeal]:
        """Keep only pool meals that still ground to safe against the live index.

        A meal joined the pool code-verified and admin-approved, but the index it
        was graded against can change, so the ``verified`` badge is re-earned here
        rather than trusted: each meal's stored ingredients are re-graded by the
        same code that grades a live lookup, and a meal that no longer grounds to
        safe is dropped so the generation tier can fill its place. No model call,
        the verdict comes from the index. A meal carrying no ingredients grounds to
        safe by the same rule a clean lookup does, which the composer never emits
        but the code handles without a special case.
        """
        kept: list[CuratedMeal] = []
        for meal in meals:
            items = [
                (ingredient.get("name", ""), ingredient.get("category"))
                for ingredient in meal.ingredients
            ]
            lookups = await lookup_ingredients(self._service, items)
            if _grounded_verdict(lookups) is SafetyLevel.SAFE:
                kept.append(meal)
        return kept

    async def _generate_alternatives(
        self,
        dish: str,
        goal: AlternativeGoal,
        anchors: list[str],
        avoid_ingredients: list[str],
        already_chosen: list[str],
        count: int,
    ) -> list[DishAlternative]:
        """Generate fresh dish ideas grounded in the safe anchors (one model call).

        ``already_chosen`` are the verified picks already filling the list, named so
        the model does not regenerate one. For ``same_style`` the pool picks are the
        nearest dishes to the query, exactly what the model would propose unprompted,
        so without this it would collide with them and waste the slots. ``count`` is
        how many slots remain, so the model writes only what is needed. Both are a
        prompt steer, not the guarantee: the merge still dedupes and caps.
        """
        messages: list[BaseMessage] = [
            SystemMessage(self._alternatives_prompt),
            HumanMessage(
                render_prompt(
                    self._alternatives_user_template,
                    "dish_lookup/alternatives_user",
                    # The dish and the ingredient names are direct user input; the
                    # goal and count lines are code-owned. The anchors and the
                    # already-chosen names are curated DB values but are stripped too
                    # (defence in depth, harmless for real names), and every region
                    # tag stays in the strip set so user input can forge none of these
                    # sections. An empty region renders empty; the prompt handles that.
                    dish=strip_region_tags(dish, _ALTERNATIVES_TAGS),
                    excluded=strip_region_tags(", ".join(avoid_ingredients), _ALTERNATIVES_TAGS),
                    safe_anchors=strip_region_tags(", ".join(anchors), _ALTERNATIVES_TAGS),
                    already_suggested=strip_region_tags(
                        ", ".join(already_chosen), _ALTERNATIVES_TAGS
                    ),
                    goal_line=_goal_line(goal),
                    count_line=f"Suggest up to {count} alternative{'s' if count != 1 else ''}.",
                )
            ),
        ]
        log.debug("dish_lookup.alternatives_request", messages=loggable_messages(messages))
        draft = await self._structured_invoke(DishAlternativesDraft, messages, step="alternatives")
        log.debug("dish_lookup.alternatives_reply", draft=draft.model_dump())
        return _generated_alternatives(draft.alternatives)
