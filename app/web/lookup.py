"""The dish lookup: the flagship human-in-the-loop flow, as ordinary pages.

Two ways in, one thing they answer. The visitor either names a dish and lets the
model work out what usually goes into it, or lists the ingredients themselves;
from there both run the same assessment and rewrite and land on the same page — a
version of the dish with nothing avoid-level in it, or why there is none. That is
why the two entries are one flow rather than two: a dish this app puts its name to
is never one the index would refuse. The editor comes after the answer, not in
front of it, so the person still has the last word without waiting for a list they
did not ask to review.

Every step drives the JSON API's own handler, so the lookup caches, the shared-tier
charge, and the per-IP burst limit keep exactly one implementation; these routes turn
a form into that call and its refusal into page copy.

Nothing is kept server-side between steps. Everything a result page can still do —
write a recipe, fetch alternatives, save the dish — needs the assessment it came
from, so the page carries it back in one hidden field that is re-validated here.
That gives nothing away: the recipe, alternatives, and lookup-save payloads are all
client-asserted by contract already, and both the verdict and the rewritten list
were computed in code from the curated index before they were ever rendered.

The LLM provider is resolved inside the handlers rather than through ``Depends``:
a visitor who picked the shared tier without a session has to read an explanation
on the page, not the API's 401 JSON body.
"""

from typing import NamedTuple
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError
from slowapi.errors import RateLimitExceeded

from app.agents.dish_lookup import DishLookupAgent
from app.dependencies import (
    build_dish_lookup_agent,
    build_recipe_agent,
    get_current_user_optional,
    get_daily_service,
    get_dish_lookup_service,
    get_ingredient_service,
    get_meal_service,
    get_quota_service,
    get_request_llm_config,
    get_saved_meal_service,
)
from app.enums import AlternativeGoal, RewriteOutcome, SafetyLevel, SaveSource
from app.llm.errors import LLMError, LLMInvocationError, LLMRejectedError
from app.llm.request import RequestLLM
from app.models.user import User
from app.schemas.meal import (
    MAX_DISH_CHARS,
    AdaptedDish,
    Advisory,
    ConfirmedIngredient,
    DishAlternativesRequest,
    DishAlternativesResponse,
    DishAssessmentResponse,
    DishLookupRequest,
    DishRewriteRequest,
    LookupRecipeRequest,
    ProposedIngredient,
    RecipeGeneration,
)
from app.schemas.saved import SaveFromLookup
from app.schemas.usage import LLMUsage
from app.services.daily_service import DailyService
from app.services.dish_lookup_service import DishLookupService
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService
from app.services.quota_service import QuotaExceededError, QuotaService
from app.services.saved_meal_service import SavedMealService, SaveLimitReached
from app.web.deps import (
    confirmed_ingredients,
    known_categories,
    read_known_categories,
    require_user,
    templates,
)

router = APIRouter(prefix="/lookup")

# The bar for a hand-typed list. With no proposal behind it there is no
# model-vetted context at all, so a single ingredient is not worth a model call.
MANUAL_MIN_INGREDIENTS = 2

# Which half of the entry form to read. Anything else means the model works the
# ingredients out, so a post carrying only a dish name — the alternatives list, an
# old tab — is the name path by default.
MODE_OWN = "own"

# Everything a step can fail with that the visitor should read as a sentence. The
# HTTPException is the shared tier's own refusal (no session); the rest are domain
# errors the API boundary would otherwise turn into a JSON body.
_STEP_FAILURES = (HTTPException, RateLimitExceeded, QuotaExceededError, LLMError)


class LookupState(BaseModel):
    """Everything a result page holds, round-tripped through one hidden field.

    ``lookup_id`` is minted once per assessment and becomes the save's key, so
    saving the same result twice is idempotent while a fresh assessment saves as
    its own row. ``alternatives`` keeps the suggestions already fetched for each
    goal, so going back to a goal costs no second model call.

    ``adapted`` is required, not optional: every way into this flow assesses and
    rewrites in one step, so the dish the page is *about* is always the rewritten
    one. The recipe, the save, and the ingredient list follow it through the
    properties below rather than each step deciding which dish it means. A state
    from before that — a tab held open across a deploy — fails validation here and
    is sent back to the start, which is what any unreadable state does.
    """

    lookup_id: UUID = Field(default_factory=uuid4)
    result: DishAssessmentResponse
    # The list the verdict was computed from, categories included. Carried because
    # a re-check re-grounds it, and a category dropped here would miss the
    # assessment cache and pay for a second reading of the same dish.
    confirmed: list[ConfirmedIngredient]
    adapted: AdaptedDish
    recipe: RecipeGeneration | None = None
    alternatives: dict[AlternativeGoal, DishAlternativesResponse] = Field(default_factory=dict)

    @property
    def dish(self) -> str:
        """The dish this page is now about."""
        return self.adapted.name

    @property
    def verdict(self) -> SafetyLevel:
        return self.adapted.verdict

    @property
    def description(self) -> str:
        return self.adapted.explanation

    @property
    def ingredients(self) -> list[ProposedIngredient]:
        """What is actually in the dish being shown."""
        return self.adapted.ingredients

    @property
    def advisories(self) -> list[Advisory]:
        """The keep-an-eye-on notes for the dish on the page.

        A rewritten dish's notes are the index's own wording for the depends-level
        ingredients it kept, so they carry across as advisories unchanged. A dish
        nothing was rewritten in keeps the assessment's own notes: there is no
        second reading of it to prefer.
        """
        if self.adapted.outcome is RewriteOutcome.UNCHANGED:
            return self.result.advisories
        return [
            Advisory(ingredient=item.name, note=item.note)
            for item in self.adapted.cautioned_ingredients
        ]

    @property
    def model(self) -> str:
        """Which model produced what is shown; the assessment's when none rewrote it."""
        return self.adapted.model or self.result.model


class ModelCall(NamedTuple):
    """One page's model provenance and token cost, for the browser's usage tally.

    Rendered into the page the calls produced; the script adds it to the running
    total kept in the visitor's own browser. A page whose every step came from the
    cache made no call, so it is reported as none at all rather than as a call that
    cost nothing.
    """

    step: str
    model: str
    usage: LLMUsage


@router.get("", response_class=HTMLResponse)
async def entry(
    request: Request,
    dish: str = Query(default="", description="A dish name to start from."),
    mode: str = Query(default="", description="Open on the ingredient editor when 'own'."),
) -> HTMLResponse:
    """Name a dish, or list what is going into it."""
    return _entry_page(request, dish=dish.strip()[:MAX_DISH_CHARS], mode=mode)


@router.post("/check", response_class=HTMLResponse)
async def check(
    request: Request,
    dish: str = Form(),
    mode: str = Form(default=""),
    ingredient: list[str] = Form(default=[]),
    ingredient_categories: str = Form(default=""),
    user: User | None = Depends(get_current_user_optional),
    quota: QuotaService = Depends(get_quota_service),
    ingredient_service: IngredientService = Depends(get_ingredient_service),
    meals: MealService = Depends(get_meal_service),
    lookup: DishLookupService = Depends(get_dish_lookup_service),
) -> HTMLResponse:
    """Work out a version of the dish the index can support, whichever way in.

    From a name the model proposes the list first; from a list the visitor typed
    there is nothing to propose and that call is saved. Both then run the same
    assessment and rewrite, so neither way in can end on a dish the index refuses.
    """
    dish = dish.strip()
    own = mode == MODE_OWN
    # Read only when the radio asked for them, so an editor left filled by an
    # earlier attempt cannot steer a check the visitor asked the model to work out.
    listed = (
        confirmed_ingredients(ingredient, read_known_categories(ingredient_categories))
        if own
        else []
    )

    def _back(error: str | None = None, *, unrecognized: bool = False) -> HTMLResponse:
        # The rows as normalized, so a refused check shows what would actually be
        # checked rather than the blanks and repeats the editor lets through. A dish
        # nobody could place comes back on the other half of the form, because
        # listing it by hand is the only thing left to try.
        return _entry_page(
            request,
            dish=dish,
            mode=MODE_OWN if unrecognized else mode,
            ingredients=listed,
            error=error,
            unrecognized=unrecognized,
        )

    if not dish:
        return _back("Name the dish before checking it.")
    try:
        named = DishLookupRequest(dish=dish)
    except ValidationError:
        return _back(f"Keep the dish name under {MAX_DISH_CHARS} characters.")
    if own and len(listed) < MANUAL_MIN_INGREDIENTS:
        return _back(f"List at least {MANUAL_MIN_INGREDIENTS} ingredients.")

    proposed = LLMUsage()
    try:
        resolved, agent = await _lookup_agent(request, user, quota, ingredient_service, meals)
        if not own:
            proposal = await lookup.propose(named, agent=agent, resolved=resolved)
            if not proposal.recognized or not proposal.ingredients:
                # No dish in the text: say so, rather than spending the rewrite call
                # on nonsense and calling the result a version of something.
                return _back(unrecognized=True)
            listed, proposed = proposal.ingredients, proposal.usage
        rewrite = DishRewriteRequest.model_validate(
            {"dish": dish, "ingredients": [item.model_dump() for item in listed]}
        )
        assessment, adapted = await lookup.adapt(rewrite, agent=agent, resolved=resolved)
        state = LookupState(result=assessment, confirmed=rewrite.ingredients, adapted=adapted)
        goal = await _close_dishes(state, lookup=lookup, agent=agent, resolved=resolved)
    except _STEP_FAILURES as exc:
        return _back(_failure_message(exc))
    return _safe_page(
        request,
        state,
        goal=goal,
        call=_rewrite_call(
            *_rewrite_usage(state, proposed, goal=goal),
            model=adapted.model or assessment.model,
        ),
    )


@router.post("/adapt", response_class=HTMLResponse)
async def adapt_result(
    request: Request,
    state: str = Form(),
    user: User | None = Depends(get_current_user_optional),
    quota: QuotaService = Depends(get_quota_service),
    ingredients: IngredientService = Depends(get_ingredient_service),
    meals: MealService = Depends(get_meal_service),
    lookup: DishLookupService = Depends(get_dish_lookup_service),
) -> HTMLResponse:
    """Run the rewrite again for a dish whose first attempt came up short.

    The retry behind an ``exhausted`` run, and the only step that spends a call on
    a dish already on the page. It re-grounds the same confirmed list, so a second
    attempt starts from the ingredients the assessment was computed over.
    """
    current = _read_state(state)
    payload = DishRewriteRequest(dish=current.result.dish, ingredients=current.confirmed)
    try:
        resolved, agent = await _lookup_agent(request, user, quota, ingredients, meals)
        assessment, adapted = await lookup.adapt(payload, agent=agent, resolved=resolved)
        # The assessment comes back from the same cache row the page was built
        # from; taking the fresh one keeps the two halves of the page in step.
        current.result = assessment
        current.adapted = adapted
        goal = await _close_dishes(current, lookup=lookup, agent=agent, resolved=resolved)
    except _STEP_FAILURES as exc:
        return _safe_page(request, current, error=_failure_message(exc))
    return _safe_page(
        request,
        current,
        goal=goal,
        call=_rewrite_call(
            *_rewrite_usage(current, goal=goal), model=adapted.model or assessment.model
        ),
    )


@router.post("/refine", response_class=HTMLResponse)
async def refine(request: Request, state: str = Form()) -> HTMLResponse:
    """Open the version on the page back in the entry editor, ready to re-check.

    Costs no model call of its own: it hands the rewritten list to the same form
    someone typing their own list uses, so an edited version re-enters the flow as
    exactly that — their dish rather than the model's suggestion.
    """
    current = _read_state(state)
    return _entry_page(
        request,
        dish=current.adapted.name,
        mode=MODE_OWN,
        ingredients=current.adapted.ingredients,
        known=known_categories(current.adapted.ingredients),
    )


@router.post("/recipe", response_class=HTMLResponse)
async def write_recipe(
    request: Request,
    state: str = Form(),
    user: User | None = Depends(get_current_user_optional),
    quota: QuotaService = Depends(get_quota_service),
    ingredients: IngredientService = Depends(get_ingredient_service),
    lookup: DishLookupService = Depends(get_dish_lookup_service),
) -> HTMLResponse:
    """Write a recipe for the dish on the page, straight off its card.

    Nothing is persisted: the dish is not a saved meal yet, so the steps ride in
    the page's state until a save carries them along.
    """
    current = _read_state(state)
    if not current.ingredients:
        return _safe_page(request, current, error="There is no dish here to write a recipe for.")
    payload = LookupRecipeRequest(
        dish=current.dish,
        description=current.description,
        ingredients=[
            ConfirmedIngredient(name=item.name, category=item.category)
            for item in current.ingredients
        ],
        advisories=current.advisories,
    )
    try:
        resolved = await get_request_llm_config(request, user, quota)
        recipe = await lookup.recipe(
            payload, agent=build_recipe_agent(resolved, ingredients), resolved=resolved
        )
    except _STEP_FAILURES as exc:
        return _safe_page(request, current, error=_failure_message(exc))
    current.recipe = recipe
    return _safe_page(request, current, call=ModelCall("recipe", recipe.model, recipe.usage))


@router.post("/alternatives", response_class=HTMLResponse)
async def suggest_alternatives(
    request: Request,
    state: str = Form(),
    goal: AlternativeGoal = Form(),
    user: User | None = Depends(get_current_user_optional),
    quota: QuotaService = Depends(get_quota_service),
    ingredients: IngredientService = Depends(get_ingredient_service),
    meals: MealService = Depends(get_meal_service),
    lookup: DishLookupService = Depends(get_dish_lookup_service),
) -> HTMLResponse:
    """Suggest other dishes for one goal, once this one cannot be kept.

    A goal already fetched is shown from the page's own state, so switching back
    and forth between goals costs one model call each, not one per click.
    """
    current = _read_state(state)
    if goal in current.alternatives:
        return _safe_page(request, current, goal=goal)

    try:
        payload = _alternatives_request(current.result, goal)
    except ValidationError:
        return _safe_page(request, current, error="There is nothing here to suggest against.")

    try:
        resolved, agent = await _lookup_agent(request, user, quota, ingredients, meals)
        suggestions = await lookup.alternatives(payload, agent=agent, resolved=resolved)
    except _STEP_FAILURES as exc:
        return _safe_page(request, current, goal=goal, error=_failure_message(exc))
    current.alternatives[goal] = suggestions
    return _safe_page(
        request,
        current,
        goal=goal,
        call=ModelCall("alternatives", suggestions.model, suggestions.usage),
    )


@router.post("/save")
async def save_result(
    request: Request,
    state: str = Form(),
    user: User = Depends(require_user),
    service: SavedMealService = Depends(get_saved_meal_service),
    meals: MealService = Depends(get_meal_service),
    daily: DailyService = Depends(get_daily_service),
) -> Response:
    """Put the dish on the page onto the visitor's shelf, then open their copy of it.

    A rewritten dish saves as itself — its own name, its own verified list, its own
    verdict — because that, not the dish they started from, is what they will cook.
    """
    current = _read_state(state)
    if not current.ingredients:
        return _safe_page(request, current, error="There is no dish here to save.")
    # Validated rather than constructed: the schema's own normalizers are what cap
    # and dedupe a lookup save, and they read the raw shape the API receives.
    payload = SaveFromLookup.model_validate(
        {
            "source": SaveSource.LOOKUP,
            "lookup_id": current.lookup_id,
            "dish": current.dish,
            "verdict": current.verdict,
            "description": current.description,
            "ingredients": [{"name": item.name} for item in current.ingredients],
            "model": current.model,
            "recipe": current.recipe.steps if current.recipe else None,
            "recipe_model": current.recipe.model if current.recipe else None,
        }
    )
    try:
        saved, _ = await service.save(user.id, payload, meals=meals, daily=daily)
    except SaveLimitReached as exc:
        # The per-user cap is the only refusal a save from here can hit.
        return _safe_page(request, current, error=str(exc))
    except RateLimitExceeded:
        return _safe_page(request, current, error="That's a lot of saves at once. Wait a minute.")
    return RedirectResponse(f"/profile/meals/{saved.id}", status_code=status.HTTP_303_SEE_OTHER)


async def _lookup_agent(
    request: Request,
    user: User | None,
    quota: QuotaService,
    ingredients: IngredientService,
    meals: MealService,
) -> tuple[RequestLLM, DishLookupAgent]:
    """Resolve this request's provider and wire the dish-lookup agent to it.

    Both happen inside the handler's try block on purpose: a refusal here — no
    session for the shared tier, a missing key, an unknown provider — belongs on
    the page, and raised from a dependency it would reach the browser as JSON.
    """
    resolved = await get_request_llm_config(request, user, quota)
    return resolved, build_dish_lookup_agent(resolved, ingredients, meals)


def _alternatives_request(
    result: DishAssessmentResponse, goal: AlternativeGoal
) -> DishAlternativesRequest:
    """The suggestion prompt's inputs, drawn from an assessment the same way every time."""
    return DishAlternativesRequest(
        dish=result.dish,
        goal=goal,
        # Exactly the avoid-level ingredients the adaptations cover; reading them
        # off a separate filter would be a second, drifting notion of "avoid".
        avoid_ingredients=[name for entry in result.adaptations for name in entry.ingredients],
        # The dish's own safe parts, so suggestions lean on what already worked.
        prefer_ingredients=[
            item.name for item in result.ingredients if item.safety is SafetyLevel.SAFE
        ],
    )


async def _close_dishes(
    state: LookupState,
    *,
    lookup: DishLookupService,
    agent: DishLookupAgent,
    resolved: RequestLLM,
) -> AlternativeGoal | None:
    """Fetch dishes in the same style when there is no version to offer, best effort.

    Only for ``impossible``, which is read off the assessment and so cost no call
    of its own: the page would otherwise end on a refusal with nothing to do next.
    An exhausted run has already spent its rounds and may well succeed on a retry,
    so it gets the suggestions form to press rather than another call spent for it.

    Failures are swallowed on purpose. The answer the visitor asked for is already
    in hand, and losing the whole page over an extra courtesy call would be the
    worse outcome; the form on the page is the fallback.
    """
    adapted = state.adapted
    if adapted is None or adapted.outcome is not RewriteOutcome.IMPOSSIBLE:
        return None
    goal = AlternativeGoal.SAME_STYLE
    try:
        payload = _alternatives_request(state.result, goal)
        state.alternatives[goal] = await lookup.alternatives(
            payload, agent=agent, resolved=resolved
        )
    except (*_STEP_FAILURES, ValidationError):
        return None
    return goal


def _rewrite_call(*parts: LLMUsage, model: str) -> ModelCall | None:
    """One usage line for a page several calls produced, or none when all were cached.

    The rewrite path runs up to four calls behind a single form post — propose,
    assess, the rewrite itself, and the courtesy suggestions on a dead end — so
    reporting only the last would understate what the page cost. A cached step
    contributes no steps and therefore no tokens, and a page of nothing but cached
    steps reports no call at all rather than one that cost nothing.
    """
    steps = [entry for part in parts for entry in part.steps]
    if not steps:
        return None
    return ModelCall(
        "safe",
        model,
        LLMUsage(
            calls=sum(part.calls for part in parts),
            input_tokens=sum(part.input_tokens for part in parts),
            output_tokens=sum(part.output_tokens for part in parts),
            total_tokens=sum(part.total_tokens for part in parts),
            steps=steps,
        ),
    )


def _rewrite_usage(
    state: LookupState, *before: LLMUsage, goal: AlternativeGoal | None
) -> list[LLMUsage]:
    """Every step behind a rewrite page, in the order it ran."""
    parts = [*before, state.result.usage]
    if state.adapted is not None:
        parts.append(state.adapted.usage)
    if goal is not None:
        parts.append(state.alternatives[goal].usage)
    return parts


def _read_state(raw: str) -> LookupState:
    """The result the page carried back, re-validated here.

    Only a tampered field or a tab left open across a deploy can fail this, and
    neither leaves anything worth showing — so both start the flow again.
    """
    try:
        return LookupState.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="That result is no longer readable.",
            headers={"Location": "/lookup"},
        ) from None


def _entry_page(
    request: Request,
    *,
    dish: str = "",
    mode: str = "",
    ingredients: list[ProposedIngredient] | None = None,
    known: str = "",
    error: str | None = None,
    unrecognized: bool = False,
) -> HTMLResponse:
    """The one form the flow starts from, and the editor a re-check comes back to.

    Keeps the dish name, the chosen half, and the rows as normalized, so a refused
    check and an edited version both land on a form worth carrying on from.
    """
    return templates.TemplateResponse(
        request,
        "lookup.html",
        {
            "dish": dish,
            "own": mode == MODE_OWN,
            "ingredients": ingredients or [],
            "known_categories": known,
            "error": error,
            "unrecognized": unrecognized,
            "max_dish_chars": MAX_DISH_CHARS,
            "manual_minimum": MANUAL_MIN_INGREDIENTS,
            "mode_own": MODE_OWN,
        },
    )


def _safe_page(
    request: Request,
    state: LookupState,
    *,
    goal: AlternativeGoal | None = None,
    error: str | None = None,
    call: ModelCall | None = None,
) -> HTMLResponse:
    """The version of the dish the index can support, or why there is not one.

    The flow's only result page. It carries the assessment alongside the version:
    what the index had against the original is the reason the new one looks the way
    it does, and on a dead end it is the whole answer.
    """
    return templates.TemplateResponse(
        request,
        "lookup_safe.html",
        {
            "state_json": state.model_dump_json(),
            "adapted": state.adapted,
            "result": state.result,
            "recipe": state.recipe,
            "suggestions": state.alternatives.get(goal) if goal else None,
            "goal": goal,
            "goals": list(AlternativeGoal),
            "error": error,
            "call": call,
        },
    )


def _failure_message(exc: BaseException) -> str:
    """Page copy for a step the app could not complete."""
    if isinstance(exc, HTTPException):
        # The shared tier's own refusal, already written for the person reading it.
        return str(exc.detail)
    if isinstance(exc, QuotaExceededError):
        return "You're out of free AI calls for today. Your account page shows when they reset."
    if isinstance(exc, RateLimitExceeded):
        return "That's a lot of checks at once. Wait a minute, then try again."
    if isinstance(exc, LLMRejectedError):
        return str(exc)
    if isinstance(exc, LLMInvocationError):
        return "The model couldn't finish that step. Try again in a moment."
    return f"No AI provider is available for this: {exc}"
