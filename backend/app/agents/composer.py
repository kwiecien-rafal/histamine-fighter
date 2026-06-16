"""The agentic meal composer: the model owns the control flow, code owns safety.

Unlike :class:`~app.agents.dish_lookup.DishLookupAgent`, which is a code-owned
workflow (propose, disambiguate, synthesize), this is a genuine agent: given a
meal type and a set of tools, the model loops act, observe, decide, and chooses
how many ingredients to check, when to swap or abandon a dish concept, and when
it is done. Its agency earns its keep in recovery: deciding that an ingredient is
flagged and the whole dish needs rethinking is the kind of open-ended call a
hard-coded generate-check loop could not make.

Safety stays out of the model's hands. ``SubmitMeal`` is not trusted: code re-runs
the whole submitted list through the curated index and the same ``_grounded_verdict``
the dish lookup owns, and requires it to be safe before the meal is returned. The
residual gap (an ingredient the model silently omits from the list) is closed by
the admin approval downstream, per the safety invariant. The composer is honest
where a "verify the alternatives" loop would be theatre, because the meal is built
forward from a verified-safe palette rather than rescued backward from a fixed dish.

Every loop action is authored into a :class:`TraceEvent` so the run can be replayed
as the daily board's "watch the agent think" showcase. The expensive work runs
offline (cron or the admin trigger), so cost amortizes and latency is irrelevant.
"""

from collections.abc import AsyncIterator

import structlog
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.tool import ToolCall
from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.agents.dish_lookup import _grounded_verdict
from app.agents.prompting import load_prompt, render_prompt
from app.enums import MealType, SafetyLevel
from app.llm.errors import LLMError, LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.schemas.meal import (
    MAX_CONFIRMED_INGREDIENTS,
    MAX_INGREDIENT_CHARS,
    ComposedMeal,
    FindSafeIngredients,
    LookupIngredientSafety,
    ProposedIngredient,
    ProposedIngredientDraft,
    SearchCuratedMeals,
    SubmitMeal,
    TraceEvent,
)
from app.services.ingredient_lookup import (
    LookupCandidate,
    LookupResult,
    lookup_ingredient_safety,
    lookup_ingredients,
)
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService

log = structlog.get_logger(__name__)

_SUBSTITUTE_LIMIT = 3
_SEARCH_K = 3
_MAX_TRACE_TEXT = 280
_MAX_RECIPE_STEPS = 20
_MAX_TAGS = 8
_MAX_TAG_CHARS = 40
# A hard budget on the agentic loop. Low-histamine cooking is restrictive, so a run
# may iterate; this bounds the cost and the abandon point.
_DEFAULT_MAX_ITERATIONS = 12

_NUDGE = "Use the tools to verify ingredients, then call SubmitMeal with the finished meal."
_INVOCATION_ERROR = "The language model failed while composing a meal."
_TOOLS_UNSUPPORTED = (
    "The selected model does not support tool calling, which the composer requires. "
    "Point the composer at a tool-capable provider or model."
)


class ComposerExhausted(RuntimeError):
    """The composer hit its iteration budget without submitting a safe meal.

    Expected occasionally: low-histamine cooking is restrictive, so a run may fail
    to converge. The composer runs offline, so the batch logs and skips it rather
    than failing the whole job.
    """


class ComposerAgent(BaseAgent):
    """Composes a verified-safe meal through a tool-calling loop it drives itself."""

    _invocation_error = _INVOCATION_ERROR

    def __init__(
        self,
        chat: ChatModel,
        ingredient_service: IngredientService,
        meal_service: MealService,
        *,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    ) -> None:
        super().__init__(chat)
        self._ingredient_service = ingredient_service
        self._meal_service = meal_service
        self._max_iterations = max_iterations
        self._system_prompt = render_prompt(
            load_prompt("composer/system"), "composer/system", input_tag="<brief>"
        )
        self._compose_user_template = load_prompt("composer/compose_user")

    async def compose(self, meal_type: MealType) -> ComposedMeal:
        """Compose one verified-safe meal for the meal type, or raise on no result.

        Raises:
            ComposerExhausted: the loop hit its budget without a safe submission.
            LLMInvocationError: the model failed or cannot call tools.
        """
        async for item in self._events(meal_type):
            if isinstance(item, ComposedMeal):
                log.info(
                    "composer.done",
                    meal_type=meal_type.value,
                    name=item.name,
                    ingredients=len(item.ingredients),
                    trace=len(item.reasoning_trace),
                    model=self.model_name,
                )
                return item
        # _events raises ComposerExhausted rather than finishing without a meal.
        raise ComposerExhausted(f"Composer produced no meal for {meal_type.value}.")

    async def stream(self, meal_type: MealType) -> AsyncIterator[str]:
        """Stream the reasoning trace as JSON lines, ending with the composed meal.

        The first real streaming agent: each authored ``TraceEvent`` is yielded as
        it happens and the terminal item is the ``ComposedMeal``, so the admin live
        trigger (Phase 4) can replay the run as it runs. Dish-lookup streaming stays
        unimplemented on purpose.
        """
        async for item in self._events(meal_type):
            yield item.model_dump_json()

    async def _events(self, meal_type: MealType) -> AsyncIterator[TraceEvent | ComposedMeal]:
        """Drive the tool-calling loop, yielding each authored step then the meal.

        Both ``compose`` and ``stream`` consume this, so the loop lives in one place.
        The running ``trace`` becomes the meal's ``reasoning_trace``, and each new
        event is yielded as it is appended.
        """
        self._begin_usage()
        try:
            model = self._chat.model.bind_tools(
                [LookupIngredientSafety, FindSafeIngredients, SearchCuratedMeals, SubmitMeal]
            )
        except NotImplementedError as exc:
            raise LLMInvocationError(_TOOLS_UNSUPPORTED) from exc

        messages: list[BaseMessage] = [
            SystemMessage(self._system_prompt),
            HumanMessage(
                render_prompt(
                    self._compose_user_template,
                    "composer/compose_user",
                    meal_type=meal_type.value,
                )
            ),
        ]
        trace: list[TraceEvent] = []

        for iteration in range(self._max_iterations):
            try:
                reply = await model.ainvoke(messages)
            except LLMError:
                raise
            except Exception as exc:
                raise LLMInvocationError(_INVOCATION_ERROR) from exc
            self._tally(reply, step="compose")
            messages.append(reply)
            log.debug(
                "composer.reply",
                iteration=iteration,
                tool_calls=[call["name"] for call in reply.tool_calls],
            )

            draft = self._draft_event(reply)
            if draft is not None:
                trace.append(draft)
                yield draft

            if not reply.tool_calls:
                messages.append(HumanMessage(_NUDGE))
                continue

            for call in reply.tool_calls:
                if call["name"] == SubmitMeal.__name__:
                    before = len(trace)
                    meal, feedback = await self._handle_submission(meal_type, call, trace)
                    for event in trace[before:]:
                        yield event
                    if meal is not None:
                        yield meal
                        return
                    messages.append(
                        ToolMessage(content=feedback or "", tool_call_id=_call_id(call))
                    )
                else:
                    content, event = await self._run_tool(call)
                    trace.append(event)
                    yield event
                    messages.append(ToolMessage(content=content, tool_call_id=_call_id(call)))

        log.warning(
            "composer.exhausted", meal_type=meal_type.value, iterations=self._max_iterations
        )
        raise ComposerExhausted(f"Composer exhausted {self._max_iterations} iterations.")

    async def _handle_submission(
        self, meal_type: MealType, call: ToolCall, trace: list[TraceEvent]
    ) -> tuple[ComposedMeal | None, str | None]:
        """Verify a submission in code; return the meal or feedback to revise.

        The verdict is recomputed in code from the index, never trusted from the
        model: an ``avoid`` reading on any ingredient (or one that cannot be read)
        sends it back. Appends the submit and the verify/reject events to ``trace``.
        """
        try:
            submission = SubmitMeal.model_validate(call["args"])
        except ValidationError:
            trace.append(TraceEvent(kind="reject", text="The submitted meal was malformed."))
            return None, "Your SubmitMeal arguments were malformed. Resend the full meal."

        ingredients = self._normalize_ingredients(submission.ingredients)
        trace.append(
            TraceEvent(
                kind="submit",
                text=f"Submitting '{submission.name}' with {len(ingredients)} "
                "ingredients for verification.",
            )
        )
        if not ingredients:
            trace.append(TraceEvent(kind="reject", text="No usable ingredients in the submission."))
            return None, "The submission listed no usable ingredients. List them and resubmit."

        lookups = await lookup_ingredients(
            self._ingredient_service, [(item.name, item.category) for item in ingredients]
        )
        blockers = self._blockers(lookups)
        if blockers:
            name, reason = blockers[0]
            trace.append(
                TraceEvent(
                    kind="reject",
                    text=f"Rejected '{submission.name}': {name} is {reason}.",
                    ingredient=name,
                    compatibility=reason,
                )
            )
            return None, self._reject_feedback(blockers)

        trace.append(
            TraceEvent(
                kind="verify",
                text=f"Verified all {len(ingredients)} ingredients against the index. "
                f"'{submission.name}' is safe.",
            )
        )
        return (
            ComposedMeal(
                name=submission.name.strip(),
                meal_type=meal_type,
                description=submission.description.strip(),
                ingredients=ingredients,
                recipe=self._normalize_recipe(submission.recipe),
                tags=self._normalize_tags(submission.tags),
                reasoning_trace=list(trace),
                model=self.model_name,
            ),
            None,
        )

    async def _run_tool(self, call: ToolCall) -> tuple[str, TraceEvent]:
        """Execute one read tool, returning the model-facing result and a trace event.

        Tools only read the local database, never make an external call. The result
        string is what the model reads next; the event is the human-facing line.
        """
        name = call["name"]
        args = call["args"]
        if name == LookupIngredientSafety.__name__:
            ingredient = _arg_str(args, "ingredient")
            category = _arg_str(args, "category") or None
            result = await lookup_ingredient_safety(self._ingredient_service, ingredient, category)
            reading = self._reading(result)
            event = TraceEvent(
                kind="check",
                text=f"Checked {ingredient or 'an ingredient'}: {reading}.",
                ingredient=ingredient or None,
                compatibility=reading,
            )
            return self._lookup_content(ingredient, result, reading), event

        if name == FindSafeIngredients.__name__:
            category = _arg_str(args, "category")[:MAX_INGREDIENT_CHARS]
            rows = await self._ingredient_service.find_substitutes(
                category, limit=_SUBSTITUTE_LIMIT
            )
            names = [row.name for row in rows]
            joined = ", ".join(names)
            if names:
                return (
                    f"Well-tolerated options in '{category}': {joined}.",
                    TraceEvent(kind="swap", text=f"Found safe options for {category}: {joined}."),
                )
            return (
                f"No well-tolerated options indexed for '{category}'.",
                TraceEvent(kind="swap", text=f"No safe options indexed for {category}."),
            )

        if name == SearchCuratedMeals.__name__:
            query = _arg_str(args, "query")[: MealService.max_query_length]
            wanted = _parse_meal_type(args.get("meal_type"))
            matches = (
                await self._meal_service.search(query, meal_type=wanted, k=_SEARCH_K)
                if query
                else []
            )
            names = [match.meal.name for match in matches]
            joined = ", ".join(names) if names else "nothing similar yet"
            return (
                f"Approved meals similar to '{query}': {joined}.",
                TraceEvent(
                    kind="check", text=f"Checked the approved pool for '{query}': {joined}."
                ),
            )

        log.warning("composer.unknown_tool", tool=name)
        return (
            f"Unknown tool '{name}'.",
            TraceEvent(kind="check", text=f"Ignored an unrecognised tool call '{name}'."),
        )

    def _blockers(self, lookups: list[LookupResult]) -> list[tuple[str, str]]:
        """The ingredients that fail the index check, each with the reason, in order.

        Uses ``_grounded_verdict`` per ingredient (the dish lookup owns it), so an
        ``avoid`` reading blocks. A lookup that errored read nothing, so it cannot
        be evidence of safety and blocks too.
        """
        blockers: list[tuple[str, str]] = []
        for lookup in lookups:
            if lookup.error:
                blockers.append((lookup.ingredient, "unverifiable"))
                continue
            level = _grounded_verdict([lookup])
            if level is not SafetyLevel.SAFE:
                blockers.append((lookup.ingredient, level.value))
        return blockers

    @staticmethod
    def _reject_feedback(blockers: list[tuple[str, str]]) -> str:
        listed = "; ".join(f"{name} ({reason})" for name, reason in blockers)
        return (
            f"These ingredients are not index-safe: {listed}. "
            "Swap each for a well-tolerated alternative or drop it, then resubmit."
        )

    @staticmethod
    def _reading(result: LookupResult) -> str:
        """One ingredient's index reading as a short word for the model and the trace."""
        if result.error:
            return "unverifiable"
        if not result.found:
            return "not indexed"
        return _grounded_verdict([result]).value

    def _lookup_content(self, ingredient: str, result: LookupResult, reading: str) -> str:
        if result.error:
            return f"{ingredient}: could not be read from the index, so treat it as unknown."
        if not result.found:
            return f"{ingredient}: no entry in the histamine index, so no known concern."
        rows = "; ".join(self._row_summary(candidate) for candidate in result.candidates)
        return f"{ingredient}: {reading} ({rows})."

    @staticmethod
    def _row_summary(candidate: LookupCandidate) -> str:
        parts = [f"{candidate.name} {candidate.compatibility}"]
        if candidate.mechanisms:
            parts.append("mechanisms: " + ", ".join(candidate.mechanisms))
        return ", ".join(parts)

    @staticmethod
    def _draft_event(reply: AIMessage) -> TraceEvent | None:
        """Capture the model's own reasoning text as a draft step, when it wrote any."""
        text = reply.content.strip() if isinstance(reply.content, str) else ""
        return TraceEvent(kind="draft", text=text[:_MAX_TRACE_TEXT]) if text else None

    @staticmethod
    def _normalize_ingredients(drafts: list[ProposedIngredientDraft]) -> list[ProposedIngredient]:
        """Trim and truncate each ingredient, drop blanks and duplicates, cap the count."""
        kept: list[ProposedIngredient] = []
        seen: set[str] = set()
        for draft in drafts:
            name = draft.name.strip()[:MAX_INGREDIENT_CHARS].rstrip()
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            category = (draft.category or "").strip()[:MAX_INGREDIENT_CHARS].rstrip()
            kept.append(ProposedIngredient(name=name, category=category or None))
            if len(kept) == MAX_CONFIRMED_INGREDIENTS:
                break
        return kept

    @staticmethod
    def _normalize_recipe(steps: list[str]) -> list[str] | None:
        cleaned = [step.strip() for step in steps if step.strip()][:_MAX_RECIPE_STEPS]
        return cleaned or None

    @staticmethod
    def _normalize_tags(tags: list[str]) -> list[str]:
        kept: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            cleaned = tag.strip()[:_MAX_TAG_CHARS].rstrip()
            if cleaned and cleaned.casefold() not in seen:
                seen.add(cleaned.casefold())
                kept.append(cleaned)
            if len(kept) == _MAX_TAGS:
                break
        return kept


def _arg_str(args: dict[str, object], key: str) -> str:
    value = args.get(key)
    return value.strip() if isinstance(value, str) else ""


def _parse_meal_type(raw: object) -> MealType | None:
    if isinstance(raw, str):
        try:
            return MealType(raw.strip().lower())
        except ValueError:
            return None
    return None


def _call_id(call: ToolCall) -> str:
    return call.get("id") or ""
