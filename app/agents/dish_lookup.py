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
from app.agents.meal_verification import MealVerification, verify_meal
from app.agents.prompting import load_prompt, render_prompt, strip_region_tags
from app.enums import (
    AdaptationAction,
    AlternativeGoal,
    CulinaryRole,
    DishIntegrity,
    HistamineMechanism,
    RewriteOutcome,
    SafetyLevel,
)
from app.llm.errors import LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.models import CuratedMeal
from app.schemas.meal import (
    MAX_ADVISORY_CHARS,
    MAX_ALTERNATIVES,
    MAX_CONFIRMED_INGREDIENTS,
    MAX_DESCRIPTION_CHARS,
    MAX_DISH_CHARS,
    MAX_DISH_STYLE_CHARS,
    MAX_INGREDIENT_CHARS,
    MAX_PITCH_CHARS,
    MAX_REASON_CHARS,
    Adaptation,
    AdaptationDraft,
    AdaptedDish,
    AdaptedDishDraft,
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
    IngredientChange,
    IngredientChangeDraft,
    IngredientProposalResponse,
    ProposedIngredient,
    ProposedIngredientDraft,
    ProposedIngredients,
)
from app.services.ingredient_lookup import (
    COMPATIBILITY_SAFETY,
    SUBSTITUTE_LIMIT,
    LookupResult,
    candidates_safety,
    grounded_verdict,
    lookup_ingredients,
    more_cautious,
    resolve_levels,
    worst_risky,
)
from app.services.ingredient_service import IngredientMatch, IngredientService
from app.services.meal_service import MealService

log = structlog.get_logger(__name__)

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
_ADAPT_TAGS = ("dish_text", "original_ingredients", "problems", "feedback")

# How many times the rewrite may be sent back before the attempt is abandoned. Two
# is deliberate: the first list is the model's real answer and the second is it
# acting on named blockers, while a third mostly buys latency on a request a person
# is waiting through. Giving up is not a dead end — the alternatives pivot follows.
_MAX_ADAPT_ROUNDS = 2

# Stands in for a removal the model listed no reason for, and for an original
# ingredient it dropped without accounting for at all.
_DROPPED_REASON = "Left out of this version."
_NO_INGREDIENTS_FEEDBACK = (
    "Your last answer listed no usable ingredients. Return the new version's complete "
    "ingredient list, one ingredient per entry."
)


def _goal_line(goal: AlternativeGoal) -> str:
    """The code-owned prompt line for a goal; the enum value is never interpolated."""
    match goal:
        case AlternativeGoal.ANY_MEAL:
            return "Suggest any satisfying meals; they need not resemble the original dish."
        case AlternativeGoal.SAME_STYLE:
            return "Suggest dishes in the same style and format as the original."
        case AlternativeGoal.SIMILAR_FLAVOURS:
            return "Suggest dishes with a similar flavour profile, even in a different format."
    assert_never(goal)


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


def _matches_safety(matches: list[IngredientMatch]) -> SafetyLevel:
    """The risk a set of index matches implies, used to vet a proposed swap."""
    return resolve_levels(
        {
            COMPATIBILITY_SAFETY[match.ingredient.compatibility]
            for match in matches
            if match.ingredient.compatibility is not None
        }
    )


def _format_flagged(flagged: list[FlaggedIngredient]) -> str:
    """The flagged ingredients as labelled lines for the synthesis prompt."""
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
    """The ambiguous lookups as labelled lines for the disambiguation prompt."""
    lines: list[str] = []
    for lookup in lookups:
        rows = ", ".join(
            f"{candidate.name} ({candidate.category})" if candidate.category else candidate.name
            for candidate in lookup.candidates
        )
        lines.append(f"- {lookup.ingredient}: {rows}")
    return "\n".join(lines)


def _clipped(value: str, limit: int = MAX_INGREDIENT_CHARS) -> str:
    return value.strip()[:limit].rstrip()


def _clipped_pitch(value: str, limit: int = MAX_PITCH_CHARS) -> str:
    """Clip a pitch on a word boundary, marking a real cut with an ellipsis."""
    text = value.strip()
    if len(text) <= limit:
        return text
    head = text[: limit - 1].rstrip()
    boundary = head.rsplit(" ", 1)[0] if " " in head else head
    return f"{boundary.rstrip()}…"


def _normalized(items: list[ProposedIngredientDraft]) -> list[ProposedIngredient]:
    """Degrade the model's draft items into valid response items."""
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
    """One confirmed ingredient's reading for the per-ingredient badge."""
    if lookup.error:
        return IngredientAssessment(name=name, safety=SafetyLevel.DEPENDS, found=False, error=True)
    worst = worst_risky(lookup.candidates)
    return IngredientAssessment(
        name=name,
        safety=candidates_safety(lookup.candidates),
        found=lookup.found,
        matched_on=lookup.matched_on,
        mechanisms=list(worst.mechanisms) if worst else [],
    )


def _parse_role(value: str) -> CulinaryRole | None:
    """Parse a role, or ``None`` when the model wrote something off-enum."""
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
    """Degrade the model's adaptation drafts into valid entries."""
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
    """One advisory per depends-level ingredient, model prose preferred."""
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
    """Approved-pool meals as verified suggestions; the description is the pitch."""
    kept: list[DishAlternative] = []
    for meal in meals:
        name = _clipped(meal.name, MAX_DISH_CHARS)
        if not name:
            continue
        kept.append(
            DishAlternative(name=name, pitch=_clipped_pitch(meal.description), source="verified")
        )
    return kept


def _generated_alternatives(items: list[AlternativeDraft]) -> list[DishAlternative]:
    """The model's fresh ideas, clipped; blanks dropped."""
    kept: list[DishAlternative] = []
    for item in items:
        name = _clipped(item.name, MAX_DISH_CHARS)
        if not name:
            continue
        kept.append(
            DishAlternative(name=name, pitch=_clipped_pitch(item.pitch), source="generated")
        )
    return kept


def _take_alternatives(
    kept: list[DishAlternative], seen: set[str], items: Iterable[DishAlternative]
) -> None:
    """Append items new by casefolded name into ``kept``, up to MAX_ALTERNATIVES."""
    for item in items:
        if len(kept) == MAX_ALTERNATIVES:
            return
        key = item.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)


def _format_problems(adaptations: list[Adaptation]) -> str:
    """The grounded adaptations as labelled lines for the rewrite prompt."""
    lines: list[str] = []
    for entry in adaptations:
        match entry.action:
            case AdaptationAction.SWAP:
                action = f"swap for {entry.swap}"
            case AdaptationAction.OMIT:
                action = "leave it out"
            case AdaptationAction.NO_SAFE_SWAP:
                action = "no safe replacement — build the dish without it"
            case _:
                assert_never(entry.action)
        names = " + ".join(entry.ingredients)
        lines.append(f"- {names} — {entry.role.value}; {action}; {entry.reason}")
    return "\n".join(lines) if lines else "None."


def _normalized_changes(
    drafts: list[IngredientChangeDraft],
    original: list[ConfirmedIngredient],
    adapted: list[ProposedIngredient],
) -> list[IngredientChange]:
    """Degrade the model's change lines into a diff that cannot misdescribe the dish."""
    originals = {item.name.casefold(): item.name for item in original}
    kept = {item.name.casefold() for item in adapted}
    replacements = {item.name.casefold(): item.name for item in adapted}
    changes: list[IngredientChange] = []
    covered: set[str] = set()
    for draft in drafts:
        key = _clipped(draft.original).casefold()
        if key not in originals or key in kept or key in covered:
            continue
        covered.add(key)
        changes.append(
            IngredientChange(
                original=originals[key],
                replacement=replacements.get(_clipped(draft.replacement).casefold()),
                reason=_clipped(draft.reason, MAX_REASON_CHARS) or _DROPPED_REASON,
            )
        )
    changes.extend(
        IngredientChange(original=name, replacement=None, reason=_DROPPED_REASON)
        for key, name in originals.items()
        if key not in kept and key not in covered
    )
    return changes


def _adapt_feedback(verification: MealVerification) -> str:
    """What to send back when a rewritten list did not clear the index."""
    listed = "; ".join(f"{name} ({reading})" for name, reading in verification.blockers)
    return (
        f"These ingredients on your last list could not be cleared against the index: "
        f"{listed}. Replace exactly those with well-tolerated alternatives, keep the rest "
        "of the list as it was, and return the complete list again."
    )


def _blocking_ingredients(adaptations: list[Adaptation]) -> list[str]:
    """The core ingredients that cost the dish its identity, for the dead-end page."""
    return [
        name
        for entry in adaptations
        if entry.role is CulinaryRole.CORE and entry.action is AdaptationAction.NO_SAFE_SWAP
        for name in entry.ingredients
    ]


def _integrity(adaptations: list[Adaptation]) -> DishIntegrity:
    """Grade what the adaptations do to the dish's identity."""
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
        self._adapt_prompt = render_prompt(
            load_prompt("dish_lookup/adapt_system"),
            "dish_lookup/adapt_system",
            input_tag="<dish_text>",
        )
        self._adapt_user_template = load_prompt("dish_lookup/adapt_user")

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
        # Either signal means unrecognized: the explicit flag, or a list that
        # normalized to nothing (a model ignoring the flag still returns junk-free).
        recognized = proposal.recognized and bool(ingredients)
        # Counts only, never names: this always-on line carries no user content.
        log.info(
            "dish_lookup.proposed",
            proposed=len(proposal.ingredients),
            kept=len(ingredients),
            recognized=recognized,
            model=self._chat.model_name,
        )
        return IngredientProposalResponse(
            dish=dish,
            recognized=recognized,
            ingredients=ingredients if recognized else [],
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
        verdict = grounded_verdict(grounded)
        if len(grounded) < len(lookups):
            verdict = more_cautious(verdict, SafetyLevel.DEPENDS)
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
        # material, depends-level ones only warrant a note. candidates_safety
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
            dish_style=_clipped(draft.dish_style, MAX_DISH_STYLE_CHARS) or None,
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
        """Drop clearly wrong rows from the ambiguous lookups, verdict invariant."""
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
            if candidates_safety(kept) is not candidates_safety(lookup.candidates):
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
        """Summarise the risky ingredients for the synthesis step."""
        flagged: list[FlaggedIngredient] = []
        for lookup in lookups:
            if lookup.error:
                flagged.append(
                    FlaggedIngredient(
                        ingredient=lookup.ingredient, severity=SafetyLevel.DEPENDS, error=True
                    )
                )
                continue
            worst = worst_risky(lookup.candidates)
            if worst is None:
                continue
            flagged.append(
                FlaggedIngredient(
                    ingredient=lookup.ingredient,
                    severity=candidates_safety(lookup.candidates),
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
                entry.category, limit=SUBSTITUTE_LIMIT
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
        """Vet every proposed swap against the index; never invent one."""
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

    async def adapt(
        self,
        dish: str,
        ingredients: list[ConfirmedIngredient],
        assessment: DishAssessmentResponse,
    ) -> AdaptedDish:
        """Rewrite the assessed dish into a version the curated index can support."""
        self._begin_usage()
        if not assessment.adaptations:
            return self._unchanged_dish(ingredients, assessment)
        if assessment.integrity is DishIntegrity.LOST:
            return self._no_version(assessment, _blocking_ingredients(assessment.adaptations))

        feedback = ""
        blocked: list[str] = []
        for attempt in range(_MAX_ADAPT_ROUNDS):
            draft = await self._draft_adaptation(dish, ingredients, assessment, feedback)
            proposed = _normalized(draft.ingredients)
            if not proposed:
                log.warning("dish_lookup.adapt_empty", attempt=attempt, model=self.model_name)
                feedback, blocked = _NO_INGREDIENTS_FEEDBACK, []
                continue

            # One set of lookups serves both the gate and the verdict, so the list
            # that ships and the level it ships at are read from the same rows.
            lookups = await lookup_ingredients(
                self._service, [(item.name, item.category) for item in proposed]
            )
            verification = verify_meal(lookups)
            if verification.is_safe:
                return self._adapted_dish(
                    ingredients, assessment, draft, proposed, lookups, verification
                )

            blocked = [name for name, _ in verification.blockers]
            feedback = _adapt_feedback(verification)
            log.warning(
                "dish_lookup.adapt_rejected",
                attempt=attempt,
                blocked=[f"{name} ({reading})" for name, reading in verification.blockers],
                model=self.model_name,
            )

        log.warning("dish_lookup.adapt_exhausted", rounds=_MAX_ADAPT_ROUNDS, model=self.model_name)
        return self._no_version(assessment, blocked, outcome=RewriteOutcome.EXHAUSTED)

    async def _draft_adaptation(
        self,
        dish: str,
        ingredients: list[ConfirmedIngredient],
        assessment: DishAssessmentResponse,
        feedback: str,
    ) -> AdaptedDishDraft:
        """One rewrite attempt; ``feedback`` is empty first time and names blockers after."""
        messages: list[BaseMessage] = [
            SystemMessage(self._adapt_prompt),
            HumanMessage(
                render_prompt(
                    self._adapt_user_template,
                    "dish_lookup/adapt_user",
                    # The dish text and the ingredient names are user input, and the
                    # feedback names ingredients the model itself wrote; none may
                    # close its own region or forge a sibling's.
                    dish=strip_region_tags(dish, _ADAPT_TAGS),
                    ingredients=strip_region_tags(
                        ", ".join(item.name for item in ingredients), _ADAPT_TAGS
                    ),
                    problems=strip_region_tags(
                        _format_problems(assessment.adaptations), _ADAPT_TAGS
                    ),
                    feedback=strip_region_tags(feedback or "None.", _ADAPT_TAGS),
                )
            ),
        ]
        log.debug("dish_lookup.adapt_request", messages=loggable_messages(messages))
        draft = await self._structured_invoke(AdaptedDishDraft, messages, step="adapt")
        log.debug("dish_lookup.adapt_reply", draft=draft.model_dump())
        return draft

    def _adapted_dish(
        self,
        ingredients: list[ConfirmedIngredient],
        assessment: DishAssessmentResponse,
        draft: AdaptedDishDraft,
        proposed: list[ProposedIngredient],
        lookups: list[LookupResult],
        verification: MealVerification,
    ) -> AdaptedDish:
        """Assemble a cleared rewrite: the model's prose over code's own readings."""
        verdict = grounded_verdict(lookups)
        log.info(
            "dish_lookup.adapted",
            dish=assessment.dish,
            verdict=verdict.value,
            ingredients=len(proposed),
            unverified=len(verification.unverified),
            cautioned=len(verification.cautioned),
            model=self.model_name,
        )
        return AdaptedDish(
            dish=assessment.dish,
            name=assessment.dish,
            outcome=RewriteOutcome.ADAPTED,
            explanation=_clipped(draft.explanation, MAX_DESCRIPTION_CHARS),
            ingredients=proposed,
            changes=_normalized_changes(draft.changes, ingredients, proposed),
            verdict=verdict,
            unverified_ingredients=verification.unverified,
            cautioned_ingredients=verification.cautioned,
            model=self.model_name,
            usage=self._collect_usage(),
        )

    def _unchanged_dish(
        self, ingredients: list[ConfirmedIngredient], assessment: DishAssessmentResponse
    ) -> AdaptedDish:
        """The dish as it stands, when the index flags nothing avoid-level to replace."""
        return AdaptedDish(
            dish=assessment.dish,
            name=assessment.dish,
            outcome=RewriteOutcome.UNCHANGED,
            explanation=assessment.explanation,
            ingredients=[
                ProposedIngredient(name=item.name, category=item.category) for item in ingredients
            ],
            verdict=assessment.verdict,
            model="",
            usage=self._collect_usage(),
        )

    def _no_version(
        self,
        assessment: DishAssessmentResponse,
        blocked: list[str],
        outcome: RewriteOutcome = RewriteOutcome.IMPOSSIBLE,
    ) -> AdaptedDish:
        """A dead end, and which kind it is."""
        if outcome is RewriteOutcome.IMPOSSIBLE:
            listed = ", ".join(blocked) or "what makes it itself"
            explanation = (
                f"This dish rests on {listed}, and the index has nothing that replaces it "
                "without turning the dish into something else."
            )
        else:
            explanation = "We could not put together a version of this dish that clears the index."
        return AdaptedDish(
            dish=assessment.dish,
            name=assessment.dish,
            outcome=outcome,
            explanation=explanation,
            verdict=assessment.verdict,
            blocked_ingredients=blocked,
            model=self.model_name if outcome is RewriteOutcome.EXHAUSTED else "",
            usage=self._collect_usage(),
        )

    async def _safe_anchors(
        self, avoid_ingredients: list[str], prefer_ingredients: list[str]
    ) -> list[str]:
        """Well-tolerated ingredients to steer the suggestions toward."""
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
                substitutes = await self._service.find_substitutes(category, limit=SUBSTITUTE_LIMIT)
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
        """Each excluded ingredient's index category, best match only, deduped."""
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
        """Suggest different dishes once this one cannot keep its identity."""
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
        # The verified tier is additive: a DB blip or embedder fault while retrieving
        # or re-grading must not sink a response the generation tier can still serve,
        # so it degrades to an empty pool. A ValueError is the meal service's
        # deliberate caller-bug signal (bad k, over-long query) and surfaces rather
        # than masquerading as an empty pool.
        try:
            picks = await self._verified_picks(goal, dish, anchors, avoid_ingredients)
            verified = _verified_alternatives(await self._still_safe(picks))
        except ValueError:
            raise
        except Exception:
            log.warning("dish_lookup.alternatives.retrieval_failed", goal=goal.value, exc_info=True)
            verified = []

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
        """Retrieve approved-pool meals for the goal; the goal picks the query axis."""
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
        """Keep only pool meals that still ground to safe against the live index."""
        kept: list[CuratedMeal] = []
        for meal in meals:
            items = [
                (ingredient.get("name", ""), ingredient.get("category"))
                for ingredient in meal.ingredients
            ]
            lookups = await lookup_ingredients(self._service, items)
            if grounded_verdict(lookups) is SafetyLevel.SAFE:
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
        """Generate fresh dish ideas grounded in the safe anchors (one model call)."""
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
