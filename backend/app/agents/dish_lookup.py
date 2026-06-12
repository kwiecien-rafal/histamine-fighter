"""The flagship dish-lookup agent: propose, confirm, assess.

The flow is human-in-the-loop. ``propose`` decomposes a dish into a candidate
ingredient list from the model's own culinary knowledge; the user reviews and
edits that list; ``assess`` reads each confirmed ingredient from the curated
index, computes the verdict in code, and has the model write the explanation
and swaps that justify it.

The index is a *risk registry*: it records the ingredients that matter for
histamine intolerance — mostly ones to avoid, plus some noted as well tolerated
— so an ingredient absent from it carries no known risk. The verdict is the
most cautious risk the index records across the confirmed ingredients. The
model never decides it, so the verdict and the prose can never disagree, and
every suggested swap is checked against the index before it ships.

User confirmation is what closes the enumeration gap the old tool-calling loop
had: the index is authoritative for *scoring* ingredients, not for
*enumerating* them, and a decomposition the model got wrong is now the user's
to fix rather than silently trusted. One floor remains: a lookup that *errored*
read nothing, so it is not evidence of safety — any errored lookup keeps the
verdict at "depends" or worse.
"""

from collections.abc import AsyncIterator
from typing import Any, cast

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.base import BaseAgent, loggable_messages
from app.agents.prompting import load_prompt, render_prompt, strip_closing_tag
from app.enums import Compatibility, SafetyLevel
from app.llm.errors import LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.schemas.meal import (
    MAX_CONFIRMED_INGREDIENTS,
    MAX_INGREDIENT_CHARS,
    ConfirmedIngredient,
    DishAssessmentResponse,
    DishExplanation,
    IngredientAssessment,
    IngredientProposalResponse,
    ProposedIngredient,
    ProposedIngredientDraft,
    ProposedIngredients,
    Replacement,
)
from app.services.ingredient_lookup import lookup_ingredient_safety
from app.services.ingredient_service import IngredientMatch, IngredientService

log = structlog.get_logger(__name__)

_SUBSTITUTE_LIMIT = 3
_INVOCATION_ERROR = (
    "The language model failed to complete the dish lookup. "
    "If you selected a custom model, make sure it supports structured output."
)

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


def _candidates_safety(candidates: list[dict[str, Any]]) -> SafetyLevel:
    """The risk one ``lookup_ingredient_safety`` result contributes to the dish."""
    return _resolve_levels({_compatibility_safety(c["compatibility"]) for c in candidates})


def _matches_safety(matches: list[IngredientMatch]) -> SafetyLevel:
    """The risk a set of index matches implies, used to vet a proposed swap."""
    return _resolve_levels(
        {
            _COMPATIBILITY_SAFETY[match.ingredient.compatibility]
            for match in matches
            if match.ingredient.compatibility is not None
        }
    )


def _worst_risky(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One lookup's most severe risky candidate, or ``None`` when nothing is risky."""
    risky = [
        candidate
        for candidate in candidates
        if _compatibility_safety(candidate["compatibility"]) is not SafetyLevel.SAFE
    ]
    if not risky:
        return None
    return max(risky, key=lambda c: _SAFETY_SEVERITY[_compatibility_safety(c["compatibility"])])


def _format_flagged(flagged: list[dict[str, Any]]) -> str:
    """The flagged ingredients as labelled lines for the synthesis prompt.

    Prose-shaped rather than JSON: the model grounds its explanation in named
    facts it can quote ("mechanisms: high histamine") instead of decoding
    key/value structure, and the synthesis system prompt describes these labels
    directly.
    """
    if not flagged:
        return "None."
    lines: list[str] = []
    for entry in flagged:
        parts = [f"{entry['ingredient']} — {entry['compatibility']}"]
        if entry.get("category"):
            parts.append(f"category: {entry['category']}")
        if entry.get("matched_on") == "category":
            parts.append(f'flagged as a member of the indexed group "{entry["matched_as"]}"')
        if entry.get("mechanisms"):
            parts.append("mechanisms: " + ", ".join(entry["mechanisms"]))
        if entry.get("safe_options"):
            parts.append("well-tolerated swaps: " + ", ".join(entry["safe_options"]))
        lines.append(f"- {'; '.join(parts)}.")
    return "\n".join(lines)


def _grounded_verdict(lookups: list[dict[str, Any]]) -> SafetyLevel:
    """The dish verdict the index supports: the most cautious per-ingredient risk.

    Each lookup contributes one level; ingredients absent from the index return no
    candidates and add no risk. With nothing risky recorded the verdict is safe.
    """
    verdict = SafetyLevel.SAFE
    for lookup in lookups:
        verdict = _more_cautious(verdict, _candidates_safety(lookup.get("candidates", [])))
    return verdict


def _clipped(value: str) -> str:
    return value.strip()[:MAX_INGREDIENT_CHARS].rstrip()


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
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        category = _clipped(item.category) if item.category else ""
        kept.append(ProposedIngredient(name=name, category=category or None))
        if len(kept) == MAX_CONFIRMED_INGREDIENTS:
            break
    return kept


def _ingredient_assessment(name: str, lookup: dict[str, Any]) -> IngredientAssessment:
    """One confirmed ingredient's reading for the per-ingredient badge.

    A failed lookup is marked ``error`` and reads as "depends": a cautious
    default consistent with the floored dish verdict, not an index reading.
    """
    if lookup.get("error"):
        return IngredientAssessment(name=name, safety=SafetyLevel.DEPENDS, found=False, error=True)
    candidates = lookup.get("candidates", [])
    worst = _worst_risky(candidates)
    return IngredientAssessment(
        name=name,
        safety=_candidates_safety(candidates),
        found=bool(lookup.get("found")),
        matched_on=lookup.get("matched_on"),
        mechanisms=worst.get("mechanisms", []) if worst else [],
    )


class DishLookupAgent(BaseAgent):
    """Classifies a dish by grounding the verdict in curated ingredient data."""

    def __init__(self, chat: ChatModel, service: IngredientService) -> None:
        super().__init__(chat)
        self._service = service
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

    def stream(self, dish: str) -> AsyncIterator[str]:
        # Declared, not omitted, so the streaming contract stays explicit; deferred.
        raise NotImplementedError("Streaming dish lookup is not implemented yet.")

    async def propose(self, dish: str) -> IngredientProposalResponse:
        """Decompose the dish into the ingredient list the user will confirm."""
        messages: list[BaseMessage] = [
            SystemMessage(self._propose_prompt),
            HumanMessage(
                render_prompt(
                    self._propose_user_template,
                    "dish_lookup/propose_user",
                    dish=strip_closing_tag(dish, "dish"),
                )
            ),
        ]
        log.debug("dish_lookup.propose_request", messages=loggable_messages(messages))
        proposal = await self._structured_invoke(ProposedIngredients, messages)
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
            dish=dish, ingredients=ingredients, model=self._chat.model_name
        )

    async def assess(
        self, dish: str, ingredients: list[ConfirmedIngredient]
    ) -> DishAssessmentResponse:
        """Read each confirmed ingredient from the index and assemble the answer."""
        lookups = [
            await lookup_ingredient_safety(self._service, item.name, item.category)
            for item in ingredients
        ]
        log.debug(
            "dish_lookup.lookups",
            results=[
                {
                    "ingredient": lookup.get("ingredient"),
                    "found": lookup.get("found"),
                    "matched_on": lookup.get("matched_on"),
                    "candidates": [
                        (candidate["name"], candidate["compatibility"])
                        for candidate in lookup.get("candidates", [])
                    ],
                }
                for lookup in lookups
            ],
        )

        # A failed lookup (DB blip) read nothing, so it is not evidence of safety:
        # the confirmed list is complete by declaration, but its grounding is not.
        grounded = [lookup for lookup in lookups if not lookup.get("error")]
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
        await self._attach_safe_options(flagged)
        explanation = await self._synthesize(dish, ingredients, verdict, flagged)
        replacements = await self._ground_swaps(verdict, flagged, explanation.replacements)
        # checked is a count, not names, so this always-on line carries no user
        # content; drivers name only what the curated index itself flagged.
        log.info(
            "dish_lookup.verdict",
            dish=explanation.dish,
            verdict=verdict.value,
            checked=len(lookups),
            drivers=[entry["ingredient"] for entry in flagged],
            model=self._chat.model_name,
        )
        return DishAssessmentResponse(
            dish=explanation.dish,
            verdict=verdict,
            explanation=explanation.explanation,
            replacements=replacements,
            ingredients=assessments,
            model=self._chat.model_name,
        )

    def _flagged(self, lookups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Summarise the risky ingredients for the synthesis step.

        One entry per risky lookup, built from its most severe risky candidate, so
        the model writes swaps and explanations for exactly what the index flagged.
        """
        flagged: list[dict[str, Any]] = []
        for lookup in lookups:
            worst = _worst_risky(lookup.get("candidates", []))
            if worst is None:
                continue
            flagged.append(
                {
                    "ingredient": lookup.get("ingredient"),
                    "compatibility": worst["compatibility"],
                    "mechanisms": worst.get("mechanisms", []),
                    "category": worst.get("category"),
                    # How the index flagged it: a category-matched ingredient was
                    # caught as a member of the group in matched_as ("Hard Cheese"),
                    # and the synthesis step phrases it that way.
                    "matched_on": lookup.get("matched_on"),
                    "matched_as": worst["name"],
                    "safe_options": [],
                }
            )
        return flagged

    async def _attach_safe_options(self, flagged: list[dict[str, Any]]) -> None:
        """Fill each flagged ingredient's ``safe_options`` from the index by category."""
        for entry in flagged:
            category = entry.get("category")
            if not category:
                continue
            substitutes = await self._service.find_substitutes(category, limit=_SUBSTITUTE_LIMIT)
            name = str(entry["ingredient"]).lower()
            entry["safe_options"] = [sub.name for sub in substitutes if sub.name.lower() != name]

    async def _synthesize(
        self,
        dish: str,
        ingredients: list[ConfirmedIngredient],
        verdict: SafetyLevel,
        flagged: list[dict[str, Any]],
    ) -> DishExplanation:
        messages: list[BaseMessage] = [
            SystemMessage(self._synthesis_prompt),
            HumanMessage(
                render_prompt(
                    self._synthesis_user_template,
                    "dish_lookup/synthesis_user",
                    # The dish text and every ingredient name are direct user
                    # input; none may close its own data region.
                    dish=strip_closing_tag(dish, "dish_text"),
                    ingredients=strip_closing_tag(
                        ", ".join(item.name for item in ingredients), "confirmed_ingredients"
                    ),
                    flagged=strip_closing_tag(_format_flagged(flagged), "flagged_ingredients"),
                    verdict=verdict.value,
                )
            ),
        ]
        log.debug("dish_lookup.synthesis_request", messages=loggable_messages(messages))
        explanation = await self._structured_invoke(DishExplanation, messages)
        log.debug("dish_lookup.synthesis_reply", explanation=explanation.model_dump())
        return explanation

    async def _structured_invoke[SchemaT: BaseModel](
        self, schema: type[SchemaT], messages: list[BaseMessage]
    ) -> SchemaT:
        """One structured-output call with every failure mapped to the domain error.

        Including the silent one: with function-calling providers, a model that
        answers in prose instead of emitting the structured tool call yields
        ``None`` rather than raising.
        """
        structured = self._chat.model.with_structured_output(schema)
        try:
            result = await structured.ainvoke(messages)
        except Exception as exc:
            raise LLMInvocationError(_INVOCATION_ERROR) from exc
        if result is None:
            raise LLMInvocationError(_INVOCATION_ERROR)
        return cast(SchemaT, result)

    async def _ground_swaps(
        self,
        verdict: SafetyLevel,
        flagged: list[dict[str, Any]],
        proposed: list[Replacement],
    ) -> list[Replacement]:
        """Keep only swaps the index agrees are safe, and fill gaps from it.

        A safe verdict carries no swaps. Otherwise every proposed swap is checked
        against the index and dropped if it is itself flagged; any flagged
        ingredient still without a safe swap is filled from its grounded
        ``safe_options`` when the index offers one.
        """
        if verdict is SafetyLevel.SAFE:
            return []

        kept: list[Replacement] = []
        covered: set[str] = set()
        for replacement in proposed:
            if not await self._swap_is_safe(replacement.swap):
                log.warning(
                    "dish_lookup.swap_rejected",
                    ingredient=replacement.ingredient,
                    swap=replacement.swap,
                )
                continue
            kept.append(replacement)
            covered.add(replacement.ingredient.strip().lower())

        for entry in flagged:
            name = str(entry["ingredient"])
            if name.strip().lower() in covered:
                continue
            options = entry.get("safe_options") or []
            if not options:
                log.warning("dish_lookup.swap_missing", ingredient=name)
                continue
            kept.append(
                Replacement(
                    ingredient=name,
                    swap=options[0],
                    reason=f"{options[0]} is well tolerated and stands in for {name}.",
                )
            )
        return kept

    async def _swap_is_safe(self, swap: str) -> bool:
        """A swap is usable only if the index does not record a concern for it."""
        matches = await self._service.find_candidates(swap)
        return _matches_safety(matches) is SafetyLevel.SAFE
