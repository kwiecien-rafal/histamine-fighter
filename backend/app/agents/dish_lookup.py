"""The flagship dish-lookup agent: a grounded, tool-calling loop.

The agent decomposes a dish into ingredients from the model's own culinary
knowledge, then calls ``lookup_ingredient_safety`` to read each one from the
curated index. The index is a *risk registry*: it records the ingredients that
matter for histamine intolerance — mostly ones to avoid, plus some noted as well
tolerated — so an ingredient absent from it carries no known risk.

The verdict is computed in code, not by the model: it is the most cautious risk
the index records across the dish's ingredients. The model only decomposes the
dish (the loop) and, once the verdict is known, writes the explanation and swaps
that justify it (the synthesis). Keeping the safety call in code means the
verdict and the prose can never disagree, and every suggested swap is checked
against the index before it ships.

What this does *not* guarantee is completeness. The index scores the ingredients
the model surfaces; an ingredient the model never lists is never looked up and so
is never flagged. That gap is the model's recall, not the index's — the index is
authoritative for *scoring*, not for *enumerating*. It is narrowed by a prompt
that pushes for a full ingredient list, by a verdict that refuses to assert
"safe" on incomplete grounding, and by logging how many ingredients were actually
checked so a thin decomposition shows up in the logs — but it is not eliminated.
"""

import json
from collections.abc import AsyncIterator
from typing import Any, cast
from uuid import uuid4

import structlog
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable

from app.agents.base import BaseAgent, load_prompt
from app.agents.tools import build_dish_lookup_tools
from app.enums import Compatibility, SafetyLevel
from app.llm.errors import LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.schemas.meal import DishExplanation, DishLookupResponse, Replacement
from app.services.ingredient_service import IngredientMatch, IngredientService

log = structlog.get_logger(__name__)

_PROMPT_FILE = "dish_lookup.md"
_SYNTHESIS_PROMPT_FILE = "dish_lookup_synthesis.md"
_SUBSTITUTE_LIMIT = 3
_INVOCATION_ERROR = (
    "The language model failed to complete the dish lookup. "
    "If you selected a custom model, make sure it supports tool calling."
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
    """Map one tool-result compatibility string to risk (``unknown`` -> safe)."""
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


def _grounded_verdict(lookups: list[dict[str, Any]]) -> SafetyLevel:
    """The dish verdict the index supports: the most cautious per-ingredient risk.

    Each lookup contributes one level; ingredients absent from the index return no
    candidates and add no risk. With nothing risky recorded the verdict is safe.
    """
    verdict = SafetyLevel.SAFE
    for lookup in lookups:
        verdict = _more_cautious(verdict, _candidates_safety(lookup.get("candidates", [])))
    return verdict


class DishLookupAgent(BaseAgent):
    """Classifies a dish by grounding the verdict in curated ingredient data."""

    def __init__(
        self,
        chat: ChatModel,
        service: IngredientService,
        *,
        max_iterations: int = 6,
        max_tool_calls: int = 12,
    ) -> None:
        super().__init__(chat)
        self._service = service
        self._tools = build_dish_lookup_tools(service)
        self._tools_by_name = {tool.name: tool for tool in self._tools}
        self._prompt = load_prompt(_PROMPT_FILE)
        self._synthesis_prompt = load_prompt(_SYNTHESIS_PROMPT_FILE)
        self._max_iterations = max_iterations
        self._max_tool_calls = max_tool_calls

    def stream(self, **kwargs: Any) -> AsyncIterator[str]:
        # Declared, not omitted, so the streaming contract stays explicit; deferred.
        raise NotImplementedError("Streaming dish lookup is not implemented yet.")

    async def run(self, dish: str) -> DishLookupResponse:
        log.debug("dish_lookup.start", dish=dish, model=self._chat.model_name)
        log.debug("dish_lookup.system_prompt", prompt=self._prompt)
        log.debug(
            "dish_lookup.tools",
            tools=[{"name": tool.name, "description": tool.description} for tool in self._tools],
        )
        bound = self._chat.model.bind_tools(self._tools)
        messages: list[BaseMessage] = [SystemMessage(self._prompt), HumanMessage(dish)]
        lookups: list[dict[str, Any]] = []
        complete = False
        tool_calls_made = 0

        for iteration in range(self._max_iterations):
            reply = await self._invoke(bound, messages)
            messages.append(reply)
            if not reply.tool_calls:
                complete = True
                break
            for call in reply.tool_calls:
                if tool_calls_made >= self._max_tool_calls:
                    # One turn must not fan out into unbounded DB round-trips.
                    # Stop here; the run stays "incomplete" so the verdict floors.
                    log.warning("dish_lookup.tool_budget_exhausted", made=tool_calls_made)
                    break
                tool_calls_made += 1
                # A model (or odd provider) can emit a tool call with no id or a
                # malformed shape; read defensively and never let it raise here.
                raw_args = call.get("args")
                name = call.get("name") or ""
                args = raw_args if isinstance(raw_args, dict) else {}
                call_id = call.get("id") or uuid4().hex
                result = await self._run_tool(name, args)
                lookups.append(result)
                log.debug(
                    "dish_lookup.tool",
                    iteration=iteration,
                    ingredient=args.get("ingredient"),
                    found=result.get("found"),
                    candidates=[
                        (candidate["name"], candidate["compatibility"])
                        for candidate in result.get("candidates", [])
                    ],
                )
                messages.append(ToolMessage(content=json.dumps(result), tool_call_id=call_id))
            else:
                continue  # the turn dispatched fully; keep looping
            break  # the budget broke the inner loop; stop the run

        # Grounding counts only lookups that actually succeeded; a failed tool call
        # (bad args, DB blip) read nothing, so it is not evidence of safety.
        grounded = [lookup for lookup in lookups if not lookup.get("error")]
        verdict = _grounded_verdict(grounded)
        if not complete or not grounded:
            # Incomplete grounding is the same as no grounding: we have not read
            # every ingredient, so we must not assert "safe". Floor at "depends".
            verdict = _more_cautious(verdict, SafetyLevel.DEPENDS)
            log.warning(
                "dish_lookup.incomplete_grounding",
                complete=complete,
                grounded=len(grounded),
                attempted=len(lookups),
                verdict=verdict.value,
            )

        flagged = self._flagged(lookups)
        await self._attach_safe_options(flagged)
        explanation = await self._synthesize(dish, verdict, flagged)
        replacements = await self._ground_swaps(verdict, flagged, explanation.replacements)
        # checked is a count, not names, so this always-on line carries no user
        # content; a low count under a "safe" verdict flags a thin decomposition.
        log.info(
            "dish_lookup.verdict",
            dish=explanation.dish,
            verdict=verdict.value,
            checked=len(lookups),
            drivers=[entry["ingredient"] for entry in flagged],
            model=self._chat.model_name,
        )
        return DishLookupResponse(
            dish=explanation.dish,
            verdict=verdict,
            explanation=explanation.explanation,
            replacements=replacements,
            model=self._chat.model_name,
        )

    def _flagged(self, lookups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Summarise the risky ingredients for the synthesis step.

        One entry per risky lookup, built from its most severe risky candidate, so
        the model writes swaps and explanations for exactly what the index flagged.
        """
        flagged: list[dict[str, Any]] = []
        for lookup in lookups:
            candidates = lookup.get("candidates", [])
            if _candidates_safety(candidates) is SafetyLevel.SAFE:
                continue
            risky = [
                candidate
                for candidate in candidates
                if _compatibility_safety(candidate["compatibility"]) is not SafetyLevel.SAFE
            ]
            worst = max(
                risky, key=lambda c: _SAFETY_SEVERITY[_compatibility_safety(c["compatibility"])]
            )
            flagged.append(
                {
                    "ingredient": lookup.get("ingredient"),
                    "compatibility": worst["compatibility"],
                    "mechanisms": worst.get("mechanisms", []),
                    "category": worst.get("category"),
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
        self, dish: str, verdict: SafetyLevel, flagged: list[dict[str, Any]]
    ) -> DishExplanation:
        payload = {"dish": dish, "verdict": verdict.value, "flagged": flagged}
        log.debug("dish_lookup.synthesis_request", verdict=verdict.value, flagged=flagged)
        messages: list[BaseMessage] = [
            SystemMessage(self._synthesis_prompt),
            HumanMessage(json.dumps(payload, ensure_ascii=False)),
        ]
        structured = self._chat.model.with_structured_output(DishExplanation)
        try:
            result = await structured.ainvoke(messages)
        except Exception as exc:
            raise LLMInvocationError(_INVOCATION_ERROR) from exc
        explanation = cast(DishExplanation, result)
        log.debug("dish_lookup.synthesis_reply", explanation=explanation.model_dump())
        return explanation

    async def _ground_swaps(
        self, verdict: SafetyLevel, flagged: list[dict[str, Any]], proposed: list[Replacement]
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

    async def _run_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools_by_name.get(name)
        if tool is None:
            return {"error": f"Unknown tool '{name}'.", "candidates": []}
        try:
            result: dict[str, Any] = await tool.ainvoke(args)
        except Exception:  # args the tool's schema rejects, etc. — keep the loop alive
            log.warning("dish_lookup.tool_failed", tool=name, exc_info=True)
            return {"error": f"Could not run '{name}' with those arguments.", "candidates": []}
        return result

    async def _invoke(
        self, bound: Runnable[LanguageModelInput, BaseMessage], messages: list[BaseMessage]
    ) -> AIMessage:
        # Logged as deltas (start, each reply, each tool result), not re-dumped per
        # turn; these messages never carry API keys — those live in request headers.
        try:
            reply = await bound.ainvoke(messages)
        except Exception as exc:  # provider/tool-calling failure, network, timeout
            raise LLMInvocationError(_INVOCATION_ERROR) from exc
        message = cast(AIMessage, reply)
        log.debug(
            "dish_lookup.reply",
            content=message.content,
            tool_calls=[
                {"name": call["name"], "args": call["args"]} for call in message.tool_calls
            ],
        )
        return message
