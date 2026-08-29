"""The agentic meal composer: the model owns the control flow, code owns safety.

Unlike :class:`~app.agents.dish_lookup.DishLookupAgent`, which is a code-owned
workflow (propose, disambiguate, synthesize), this is a genuine agent: given a
meal type and a set of tools, the model loops act, observe, decide, and chooses
how many ingredients to check, when to swap or abandon a dish concept, and when
it is done. Its agency earns its keep in recovery: deciding that an ingredient is
flagged and the whole dish needs rethinking is the kind of open-ended call a
hard-coded generate-check loop could not make.

Safety stays out of the model's hands. ``SubmitMeal`` is not trusted: code re-runs
the whole submitted list through the curated index and the same ``grounded_verdict``
the dish lookup owns, and also scans the recipe prose for an index-flagged ingredient
written into the steps but kept off the list. Nothing the index flags as avoid can
survive. A moderately compatible ingredient may stay, capped in number and carried
with the index's own moderation note, so dishes are not stripped bare turn after turn.
What code cannot decide it does not hide: an ingredient absent from the index is
unknown, not safe, so it passes the automated gate but is recorded as unverified for
the admin to clear (the safety invariant). The composer is honest where a "verify the
alternatives" loop would be theatre, because the meal is built forward from index
readings rather than rescued backward from a fixed dish.

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
from app.agents.inspiration import InspirationBrief
from app.agents.meal_judge import MealJudgeAgent
from app.agents.meal_quality import check_structure
from app.agents.meal_verification import MealVerification
from app.agents.prompting import load_prompt, render_prompt, strip_region_tags
from app.config import settings
from app.core.term_match import TermMatcher
from app.enums import MealType, TraceReading
from app.llm.errors import LLMError, LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.schemas.meal import (
    MAX_DESCRIPTION_CHARS,
    MAX_DISH_CHARS,
    MAX_INGREDIENT_CHARS,
    ComposedMeal,
    FindSafeIngredients,
    LookupIngredientSafety,
    MealStreamItem,
    SearchCuratedMeals,
    SubmitMeal,
    TraceEvent,
    TraceStreamItem,
    normalize_dish_text,
    normalize_ingredients,
    normalize_recipe,
    normalize_tags,
)
from app.services.ingredient_lookup import (
    LookupCandidate,
    LookupResult,
    grounded_verdict,
    lookup_ingredient_safety,
    verify_submission,
)
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService

log = structlog.get_logger(__name__)

_SUBSTITUTE_LIMIT = 3
_SEARCH_K = 3
_MAX_TRACE_TEXT = 280
# A hard budget on the agentic loop. Low-histamine cooking is restrictive, so a run
# may iterate; this bounds the cost and the abandon point.
_DEFAULT_MAX_ITERATIONS = 12
# How many failing judge verdicts a run may spend. After the last one the next safe
# submission is accepted with the judge's concerns left in the trace: quality is
# advisory, and burning the loop budget over taste would lose the slot entirely.
_MAX_JUDGE_ROUNDS = 2

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
        max_moderate_ingredients: int | None = None,
        judge: MealJudgeAgent | None = None,
    ) -> None:
        super().__init__(chat)
        self._ingredient_service = ingredient_service
        self._meal_service = meal_service
        self._max_iterations = max_iterations
        self._judge = judge
        self._judge_threshold = settings.composer_judge_threshold
        self._judge_rounds = 0
        # Resolved here, not as a parameter default, so a settings override in a
        # test or a fork is read at construction time rather than import time.
        self._max_moderate = (
            settings.composer_max_moderate_ingredients
            if max_moderate_ingredients is None
            else max_moderate_ingredients
        )
        self._system_prompt = render_prompt(
            load_prompt("composer/system"),
            "composer/system",
            input_tag="<brief>",
            moderate_cap=str(self._max_moderate),
        )
        self._compose_user_template = load_prompt("composer/compose_user")

    async def compose(
        self, meal_type: MealType, inspiration: InspirationBrief | None = None
    ) -> ComposedMeal:
        """Compose one verified-safe meal for the meal type, or raise on no result.

        Args:
            meal_type: The slot to compose.
            inspiration: A code-drawn direction the model starts from, so runs vary
                by construction rather than by sampling luck. ``None`` composes free.

        Raises:
            ComposerExhausted: the loop hit its budget without a safe submission.
            LLMInvocationError: the model failed or cannot call tools.
        """
        async for item in self.events(meal_type, inspiration):
            if isinstance(item, ComposedMeal):
                log.info(
                    "composer.done",
                    meal_type=meal_type.value,
                    name=item.name,
                    ingredients=len(item.ingredients),
                    unverified=len(item.unverified_ingredients),
                    trace=len(item.reasoning_trace),
                    model=self.model_name,
                )
                return item
        # events raises ComposerExhausted rather than finishing without a meal.
        raise ComposerExhausted(f"Composer produced no meal for {meal_type.value}.")

    async def stream(
        self, meal_type: MealType, inspiration: InspirationBrief | None = None
    ) -> AsyncIterator[str]:
        """Stream the run as discriminated JSON lines: trace steps, then the meal.

        The ``BaseAgent`` streaming contract, implemented so it cannot be silently
        dropped (CLAUDE section 8). The live admin SSE path does not go through here:
        :class:`~app.services.composer_streamer.ComposerStreamer` consumes ``events()``
        directly, because it needs the trace-carrying ``ComposedMeal`` to persist. Each
        step is a ``TraceStreamItem`` and the terminal item a ``MealStreamItem`` whose
        meal omits the trace (the client assembled it from the steps), so a consumer
        switches on ``type`` instead of sniffing the payload shape.
        """
        async for item in self.events(meal_type, inspiration):
            if isinstance(item, ComposedMeal):
                yield MealStreamItem.of(item).model_dump_json()
            else:
                yield TraceStreamItem(event=item).model_dump_json()

    async def events(
        self, meal_type: MealType, inspiration: InspirationBrief | None = None
    ) -> AsyncIterator[TraceEvent | ComposedMeal]:
        """Drive the tool-calling loop, yielding each authored step then the meal.

        The rich core: ``compose`` and ``stream`` consume it, and so does the live
        streamer, which needs the terminal ``ComposedMeal`` (with its trace) to persist
        the run. The running ``trace`` becomes the meal's ``reasoning_trace``, and each
        new event is yielded as it is appended.
        """
        self._begin_usage()
        self._judge_rounds = 0
        try:
            model = self._chat.model.bind_tools(
                [LookupIngredientSafety, FindSafeIngredients, SearchCuratedMeals, SubmitMeal]
            )
        except NotImplementedError as exc:
            raise LLMInvocationError(_TOOLS_UNSUPPORTED) from exc

        # Brief values include prior model output (recent dish names), so region-tag
        # forgeries are stripped before the text enters the <brief> data region.
        brief_lines = (
            strip_region_tags(inspiration.prompt_lines(), ("brief",)) if inspiration else ""
        )
        messages: list[BaseMessage] = [
            SystemMessage(self._system_prompt),
            HumanMessage(
                render_prompt(
                    self._compose_user_template,
                    "composer/compose_user",
                    meal_type=meal_type.value,
                    inspiration=brief_lines,
                )
            ),
        ]
        trace: list[TraceEvent] = []
        if inspiration is not None:
            drawn = TraceEvent(
                kind="inspiration",
                text=f"Drew a direction: {inspiration.summary()}."[:_MAX_TRACE_TEXT],
            )
            trace.append(drawn)
            yield drawn
        # Loaded once: the index's avoid-level terms, scanned against any submitted
        # recipe so a flagged ingredient written into the steps is caught. Cautioned
        # (moderately compatible) terms are not scanned: a kept one may appear in
        # the steps by design.
        risky_terms = TermMatcher.from_terms(await self._ingredient_service.avoid_terms())

        for iteration in range(self._max_iterations):
            try:
                reply = await model.ainvoke(messages)
            except LLMError:
                raise
            except Exception as exc:
                raise self._invocation_failure(exc, step="compose") from exc
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
                    meal, feedback = await self._handle_submission(
                        meal_type, call, trace, risky_terms
                    )
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
        self, meal_type: MealType, call: ToolCall, trace: list[TraceEvent], risky_terms: TermMatcher
    ) -> tuple[ComposedMeal | None, str | None]:
        """Verify a submission in code; return the meal or feedback to revise.

        The check is recomputed in code from the index, never trusted from the
        model: a risky reading on any listed ingredient (or one that cannot be
        read), or an index-flagged ingredient written into the recipe, sends it
        back. Ingredients absent from the index pass but are recorded as
        unverified. Appends the submit and the verify/reject events to ``trace``.
        """
        try:
            submission = SubmitMeal.model_validate(call["args"])
        except ValidationError:
            trace.append(TraceEvent(kind="reject", text="The submitted meal was malformed."))
            return None, "Your SubmitMeal arguments were malformed. Resend the full meal."

        ingredients = normalize_ingredients(
            (draft.name, draft.category) for draft in submission.ingredients
        )
        recipe = normalize_recipe(submission.recipe)
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

        verification = await verify_submission(
            self._ingredient_service, ingredients, recipe, risky_terms=risky_terms
        )
        if not verification.is_safe:
            trace.append(self._reject_event(submission.name, verification))
            return None, self._reject_feedback(verification)

        cautioned_names = ", ".join(item.name for item in verification.cautioned)
        if len(verification.cautioned) > self._max_moderate:
            trace.append(
                TraceEvent(
                    kind="reject",
                    text=f"Rejected '{submission.name}': {len(verification.cautioned)} "
                    f"moderately compatible ingredients ({cautioned_names}), "
                    f"at most {self._max_moderate} may stay.",
                )
            )
            return None, (
                f"The dish keeps {len(verification.cautioned)} moderately compatible "
                f"ingredients: {cautioned_names}. At most {self._max_moderate} may stay. "
                "Swap the rest for well-tolerated alternatives, then resubmit."
            )

        # Quality gates run only after safety passes, so safety feedback always
        # outranks quality feedback and a revision never trades one for the other.
        thin_reasons = check_structure(meal_type, ingredients, recipe)
        if thin_reasons:
            listed = "; ".join(thin_reasons)
            trace.append(
                TraceEvent(
                    kind="reject",
                    text=f"Rejected '{submission.name}' as too thin: {listed}.",
                )
            )
            return None, (
                f"The dish is safe but too thin: {listed}. Enrich it rather than "
                "trimming: add well-tolerated ingredients and fuller steps, then resubmit."
            )

        # The judge runs last and is bounded: after its rounds are spent, a safe
        # submission is accepted with the concerns already in the trace for the admin.
        if self._judge is not None and self._judge_rounds < _MAX_JUDGE_ROUNDS:
            judgement, judge_steps = await self._judge.review(
                meal_type,
                name=submission.name,
                description=submission.description,
                ingredients=ingredients,
                recipe=recipe,
                tags=normalize_tags(submission.tags),
            )
            self._calls.extend(judge_steps)
            score = judgement.score()
            if score < self._judge_threshold:
                self._judge_rounds += 1
                failed = ", ".join(judgement.failed_criteria())
                trace.append(
                    TraceEvent(
                        kind="judge",
                        text=f"Quality review scored '{submission.name}' {score}/5 "
                        f"(failed: {failed})."[:_MAX_TRACE_TEXT],
                    )
                )
                notes = " ".join(judgement.reasons)
                return None, (
                    f"A quality review scored the dish {score}/5. Failed criteria: "
                    f"{failed}. {notes} Improve it along these lines, keep every "
                    "ingredient index-safe, then resubmit."
                )
            trace.append(
                TraceEvent(
                    kind="judge",
                    text=f"Quality review passed '{submission.name}' ({score}/5).",
                )
            )

        kept_in_moderation = (
            f" Kept in moderation: {cautioned_names}." if verification.cautioned else ""
        )
        trace.append(
            TraceEvent(
                kind="verify",
                text=f"Verified all {len(ingredients)} ingredients against the index. "
                f"'{submission.name}' is safe.{kept_in_moderation}",
            )
        )
        return (
            ComposedMeal(
                name=normalize_dish_text(submission.name, max_chars=MAX_DISH_CHARS),
                meal_type=meal_type,
                description=normalize_dish_text(
                    submission.description, max_chars=MAX_DESCRIPTION_CHARS
                ),
                ingredients=ingredients,
                recipe=recipe,
                tags=normalize_tags(submission.tags),
                unverified_ingredients=verification.unverified,
                cautioned_ingredients=verification.cautioned,
                reasoning_trace=list(trace),
                model=self.model_name,
                usage=self._collect_usage(),
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
                text=f"Checked {ingredient or 'an ingredient'}: {self._reading_phrase(reading)}.",
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
                    TraceEvent(
                        kind="options", text=f"Found safe options for {category}: {joined}."
                    ),
                )
            return (
                f"No well-tolerated options indexed for '{category}'.",
                TraceEvent(kind="options", text=f"No safe options indexed for {category}."),
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
                    kind="search", text=f"Searched the approved pool for '{query}': {joined}."
                ),
            )

        log.warning("composer.unknown_tool", tool=name)
        return (
            f"Unknown tool '{name}'.",
            TraceEvent(kind="check", text=f"Ignored an unrecognised tool call '{name}'."),
        )

    @staticmethod
    def _reject_event(dish: str, verification: MealVerification) -> TraceEvent:
        """The single trace line shown when a submission is sent back.

        Leads with the most concrete reason: a flagged ingredient names the row and
        its reading, otherwise a risky recipe mention names the term.
        """
        if verification.blockers:
            ingredient, reason = verification.blockers[0]
            return TraceEvent(
                kind="reject",
                text=f"Rejected '{dish}': {ingredient} is {reason}.",
                ingredient=ingredient,
                compatibility=reason,
            )
        term = verification.recipe_flags[0]
        return TraceEvent(
            kind="reject",
            text=f"Rejected '{dish}': the recipe still uses {term}, which the index flags.",
            ingredient=term,
        )

    @staticmethod
    def _reject_feedback(verification: MealVerification) -> str:
        parts: list[str] = []
        if verification.blockers:
            listed = "; ".join(f"{name} ({reason})" for name, reason in verification.blockers)
            parts.append(
                f"These ingredients are not index-safe: {listed}. "
                "Swap each for a well-tolerated alternative or drop it."
            )
        if verification.recipe_flags:
            listed = ", ".join(verification.recipe_flags)
            parts.append(
                f"Your recipe still uses index-flagged ingredients: {listed}. "
                "Rewrite the steps to use only your verified ingredients."
            )
        parts.append("Then resubmit.")
        return " ".join(parts)

    @staticmethod
    def _reading(result: LookupResult) -> TraceReading:
        """One ingredient's index reading, as the structured token the trace carries."""
        if result.error:
            return TraceReading.UNVERIFIABLE
        if not result.found:
            return TraceReading.NOT_INDEXED
        return TraceReading(grounded_verdict([result]).value)

    @staticmethod
    def _reading_phrase(reading: TraceReading) -> str:
        """The reading as a human phrase for trace prose (no underscores)."""
        return "not indexed" if reading is TraceReading.NOT_INDEXED else reading.value

    def _lookup_content(self, ingredient: str, result: LookupResult, reading: TraceReading) -> str:
        if result.error:
            return f"{ingredient}: could not be read from the index, so treat it as unknown."
        if not result.found:
            return (
                f"{ingredient}: not in the curated index, so treat it as unknown. Prefer an "
                "ingredient you can verify as well tolerated; if you keep it, it is flagged "
                "for human review."
            )
        rows = "; ".join(self._row_summary(candidate) for candidate in result.candidates)
        if reading is TraceReading.DEPENDS:
            return (
                f"{ingredient}: depends ({rows}). You may keep it in moderation, at most "
                f"{self._max_moderate} such ingredients per dish and never as the dish's "
                "core; reflect the index note in the recipe or description, or swap it "
                "for a well-tolerated option."
            )
        return f"{ingredient}: {reading.value} ({rows})."

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
