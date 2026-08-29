"""The dish lookup: the flagship human-in-the-loop flow, as ordinary pages.

Four steps, each a form post that renders the next page — propose the ingredients,
let the visitor correct them, assess what they confirmed, and offer other dishes
when this one cannot stay itself. Every step drives the JSON API's own handler, so
the lookup cache, the shared-tier charge, and the per-IP burst limit keep exactly
one implementation; these routes turn a form into that call and its refusal into
page copy.

Nothing is kept server-side between steps. Everything the result page can still do
— write a recipe, fetch alternatives, save the dish — needs the assessment it came
from, so the page carries it back in one hidden field that is re-validated here.
That gives nothing away: the recipe, alternatives, and lookup-save payloads are all
client-asserted by contract already, and the verdict itself was computed in code
from the curated index when the dish was assessed.

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
from app.api.v1 import meals as api_meals
from app.api.v1 import saved_meals as api_saved_meals
from app.dependencies import (
    RequestLLM,
    build_dish_lookup_agent,
    build_recipe_agent,
    get_current_user_optional,
    get_daily_service,
    get_ingredient_service,
    get_lookup_cache_service,
    get_meal_service,
    get_quota_service,
    get_request_llm_config,
    get_saved_meal_service,
)
from app.enums import AdaptationAction, AlternativeGoal, DishIntegrity, SafetyLevel, SaveSource
from app.llm.errors import LLMError, LLMInvocationError, LLMRejectedError
from app.models.user import User
from app.schemas.meal import (
    MAX_CONFIRMED_INGREDIENTS,
    MAX_DISH_CHARS,
    ConfirmedIngredient,
    DishAlternativesRequest,
    DishAlternativesResponse,
    DishAssessmentRequest,
    DishAssessmentResponse,
    DishLookupRequest,
    LookupRecipeRequest,
    RecipeGeneration,
    normalize_ingredients,
)
from app.schemas.saved import SaveFromLookup
from app.schemas.usage import LLMUsage
from app.services.daily_service import DailyService
from app.services.ingredient_service import IngredientService
from app.services.lookup_cache_service import LookupCacheService
from app.services.meal_service import MealService
from app.services.quota_service import QuotaExceededError, QuotaService
from app.services.saved_meal_service import SavedMealService
from app.web.deps import (
    INGREDIENT_SEPARATOR,
    ingredient_lines,
    parse_ingredient_lines,
    require_user,
    templates,
)

router = APIRouter(prefix="/lookup")

# The bar for a hand-typed list. With no proposal behind it there is no
# model-vetted context at all, so a single ingredient is not worth a model call.
MANUAL_MIN_INGREDIENTS = 2

# Everything a step can fail with that the visitor should read as a sentence. The
# HTTPException is the shared tier's own refusal (no session); the rest are domain
# errors the API boundary would otherwise turn into a JSON body.
_STEP_FAILURES = (HTTPException, RateLimitExceeded, QuotaExceededError, LLMError)


class LookupState(BaseModel):
    """Everything the result page holds, round-tripped through one hidden field.

    ``lookup_id`` is minted once per assessment and becomes the save's key, so
    saving the same result twice is idempotent while a fresh assessment saves as
    its own row. ``alternatives`` keeps the suggestions already fetched for each
    goal, so going back to a goal costs no second model call.
    """

    lookup_id: UUID = Field(default_factory=uuid4)
    result: DishAssessmentResponse
    recipe: RecipeGeneration | None = None
    alternatives: dict[AlternativeGoal, DishAlternativesResponse] = Field(default_factory=dict)


class ModelCall(NamedTuple):
    """One model call's provenance and token cost, for the browser's usage tally.

    Rendered into the page the call produced; the script adds it to the running
    total kept in the visitor's own browser. A cached answer made no call, so it
    is reported as none at all rather than as a call that cost nothing.
    """

    step: str
    model: str
    usage: LLMUsage


@router.get("", response_class=HTMLResponse)
async def entry(request: Request) -> HTMLResponse:
    """Where a check starts: name a dish, or go straight to listing ingredients."""
    return _entry_page(request)


@router.get("/manual", response_class=HTMLResponse)
async def manual_entry(
    request: Request,
    dish: str = Query(default="", description="A dish name to carry into the editor."),
) -> HTMLResponse:
    """The editor with nothing in it, for a visitor who knows the ingredients already.

    No model call: the propose step exists to save typing, and skipping it costs
    only the dish name, which the editor asks for instead.
    """
    return _confirm_page(request, dish=dish.strip()[:MAX_DISH_CHARS], ingredients="", model="")


@router.post("/propose", response_class=HTMLResponse)
async def propose(
    request: Request,
    dish: str = Form(),
    user: User | None = Depends(get_current_user_optional),
    quota: QuotaService = Depends(get_quota_service),
    ingredients: IngredientService = Depends(get_ingredient_service),
    meals: MealService = Depends(get_meal_service),
    cache: LookupCacheService = Depends(get_lookup_cache_service),
) -> HTMLResponse:
    """Ask the model what is in the dish, then hand the list over to be corrected."""
    dish = dish.strip()
    try:
        payload = DishLookupRequest(dish=dish)
    except ValidationError:
        return _entry_page(request, dish=dish, error="Name a dish to check.")

    try:
        resolved, agent = await _lookup_agent(request, user, quota, ingredients, meals)
        proposal = await api_meals.propose_ingredients(
            request=request, payload=payload, agent=agent, resolved=resolved, cache=cache
        )
    except _STEP_FAILURES as exc:
        return _entry_page(request, dish=dish, error=_failure_message(exc))

    if not proposal.recognized or not proposal.ingredients:
        # No dish in the text: say so, rather than dropping into an empty editor
        # that would dead-end or, worse, spend a second call assessing nonsense.
        return _entry_page(request, dish=dish, unrecognized=True)
    return _confirm_page(
        request,
        dish=proposal.dish,
        ingredients=ingredient_lines(proposal.ingredients),
        model=proposal.model,
        cached=proposal.cached,
        call=None if proposal.cached else ModelCall("propose", proposal.model, proposal.usage),
    )


@router.post("/assess", response_class=HTMLResponse)
async def assess(
    request: Request,
    dish: str = Form(),
    ingredients: str = Form(),
    model: str = Form(default=""),
    user: User | None = Depends(get_current_user_optional),
    quota: QuotaService = Depends(get_quota_service),
    ingredient_service: IngredientService = Depends(get_ingredient_service),
    meals: MealService = Depends(get_meal_service),
    cache: LookupCacheService = Depends(get_lookup_cache_service),
) -> HTMLResponse:
    """Weigh the confirmed ingredients against the index and render the verdict.

    ``model`` is the model that proposed the list, empty when the visitor typed it
    themselves. It sets both the quality bar the list has to clear and whether the
    dish name is still editable.
    """
    dish = dish.strip()
    confirmed = _confirmed_ingredients(ingredients)
    minimum = 1 if model else MANUAL_MIN_INGREDIENTS

    def _back(error: str) -> HTMLResponse:
        return _confirm_page(request, dish=dish, ingredients=ingredients, model=model, error=error)

    if not dish:
        return _back("Name the dish before checking it.")
    if len(confirmed) < minimum:
        return _back(f"List at least {minimum} ingredient{'s' if minimum > 1 else ''}.")
    try:
        payload = DishAssessmentRequest.model_validate({"dish": dish, "ingredients": confirmed})
    except ValidationError:
        return _back(f"Keep the dish name under {MAX_DISH_CHARS} characters.")

    try:
        resolved, agent = await _lookup_agent(request, user, quota, ingredient_service, meals)
        result = await api_meals.assess_dish(
            request=request, payload=payload, agent=agent, resolved=resolved, cache=cache
        )
    except _STEP_FAILURES as exc:
        return _back(_failure_message(exc))
    return _result_page(
        request,
        LookupState(result=result),
        call=None if result.cached else ModelCall("assess", result.model, result.usage),
    )


@router.post("/recipe", response_class=HTMLResponse)
async def write_recipe(
    request: Request,
    state: str = Form(),
    user: User | None = Depends(get_current_user_optional),
    quota: QuotaService = Depends(get_quota_service),
    ingredients: IngredientService = Depends(get_ingredient_service),
) -> HTMLResponse:
    """Write a recipe for the assessed dish, straight off the result card.

    Nothing is persisted: the dish is not a saved meal yet, so the steps ride in
    the page's state until a save carries them along.
    """
    current = _read_state(state)
    result = current.result
    payload = LookupRecipeRequest(
        dish=result.dish,
        description=result.explanation,
        ingredients=[ConfirmedIngredient(name=item.name) for item in result.ingredients],
        advisories=result.advisories,
    )
    try:
        resolved = await get_request_llm_config(request, user, quota)
        recipe = await api_meals.generate_lookup_recipe(
            request=request,
            payload=payload,
            agent=build_recipe_agent(resolved, ingredients),
            resolved=resolved,
        )
    except _STEP_FAILURES as exc:
        return _result_page(request, current, error=_failure_message(exc))
    current.recipe = recipe
    return _result_page(request, current, call=ModelCall("recipe", recipe.model, recipe.usage))


@router.post("/alternatives", response_class=HTMLResponse)
async def suggest_alternatives(
    request: Request,
    state: str = Form(),
    goal: AlternativeGoal = Form(),
    user: User | None = Depends(get_current_user_optional),
    quota: QuotaService = Depends(get_quota_service),
    ingredients: IngredientService = Depends(get_ingredient_service),
    meals: MealService = Depends(get_meal_service),
) -> HTMLResponse:
    """Suggest other dishes for one goal, once this one cannot be kept.

    A goal already fetched is shown from the page's own state, so switching back
    and forth between goals costs one model call each, not one per click.
    """
    current = _read_state(state)
    if goal in current.alternatives:
        return _result_page(request, current, goal=goal)

    result = current.result
    payload = DishAlternativesRequest(
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
    try:
        resolved, agent = await _lookup_agent(request, user, quota, ingredients, meals)
        suggestions = await api_meals.suggest_alternatives(
            request=request, payload=payload, agent=agent, resolved=resolved
        )
    except _STEP_FAILURES as exc:
        return _result_page(request, current, goal=goal, error=_failure_message(exc))
    current.alternatives[goal] = suggestions
    return _result_page(
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
    """Put the assessed dish on the visitor's shelf, then open their copy of it."""
    current = _read_state(state)
    result = current.result
    # Validated rather than constructed: the schema's own normalizers are what cap
    # and dedupe a lookup save, and they read the raw shape the API receives.
    payload = SaveFromLookup.model_validate(
        {
            "source": SaveSource.LOOKUP,
            "lookup_id": current.lookup_id,
            "dish": result.dish,
            "verdict": result.verdict,
            "description": result.explanation,
            "ingredients": [{"name": item.name} for item in result.ingredients],
            "model": result.model,
            "recipe": current.recipe.steps if current.recipe else None,
            "recipe_model": current.recipe.model if current.recipe else None,
        }
    )
    try:
        saved = await api_saved_meals.save_meal(
            request=request,
            payload=payload,
            response=Response(),
            user=user,
            service=service,
            meal_service=meals,
            daily_service=daily,
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_409_CONFLICT:
            raise
        # The per-user cap is the only refusal a save from here can hit.
        return _result_page(request, current, error=str(exc.detail))
    except RateLimitExceeded:
        return _result_page(request, current, error="That's a lot of saves at once. Wait a minute.")
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


def _confirmed_ingredients(text: str) -> list[dict[str, str | None]]:
    """The editor's lines as a confirmed list: trimmed, deduped, and capped.

    The same normalization the propose step's own output goes through, so a list
    rewritten by hand can never be worse-formed than the one it started as.
    """
    normalized = normalize_ingredients(
        (line["name"], line["category"]) for line in parse_ingredient_lines(text)
    )
    return [item.model_dump() for item in normalized]


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


def _pivot_tone(result: DishAssessmentResponse) -> str | None:
    """How far the dish drifts from itself once adapted, or None when it holds.

    The one place the pivot is decided, so the callout above the panel and the
    panel itself can never disagree about whether it is worth offering.
    """
    if not result.adaptations:
        return None
    if result.integrity is DishIntegrity.LOST:
        return "lost"
    if result.integrity is DishIntegrity.ALTERED:
        return "altered"
    if any(entry.action is AdaptationAction.NO_SAFE_SWAP for entry in result.adaptations):
        return "unresolved"
    return None


def _entry_page(
    request: Request,
    *,
    dish: str = "",
    error: str | None = None,
    unrecognized: bool = False,
) -> HTMLResponse:
    """The dish-name form, keeping what was typed when the step got nowhere."""
    return templates.TemplateResponse(
        request,
        "lookup.html",
        {
            "dish": dish,
            "error": error,
            "unrecognized": unrecognized,
            "max_dish_chars": MAX_DISH_CHARS,
        },
    )


def _confirm_page(
    request: Request,
    *,
    dish: str,
    ingredients: str,
    model: str,
    cached: bool = False,
    error: str | None = None,
    call: ModelCall | None = None,
) -> HTMLResponse:
    """The ingredient editor: the one step where the person, not the model, decides."""
    return templates.TemplateResponse(
        request,
        "lookup_confirm.html",
        {
            "dish": dish,
            "ingredients": ingredients,
            "model": model,
            "cached": cached,
            "error": error,
            "call": call,
            "separator": INGREDIENT_SEPARATOR,
            "max_dish_chars": MAX_DISH_CHARS,
            "max_ingredients": MAX_CONFIRMED_INGREDIENTS,
            "manual_minimum": MANUAL_MIN_INGREDIENTS,
        },
    )


def _result_page(
    request: Request,
    state: LookupState,
    *,
    goal: AlternativeGoal | None = None,
    error: str | None = None,
    call: ModelCall | None = None,
) -> HTMLResponse:
    """The verdict and everything derived from it so far.

    The state is serialized once and carried by each form on the page, so the
    recipe, the fetched alternatives, and the save all see the same result.
    """
    return templates.TemplateResponse(
        request,
        "lookup_result.html",
        {
            "state_json": state.model_dump_json(),
            "result": state.result,
            "recipe": state.recipe,
            "suggestions": state.alternatives.get(goal) if goal else None,
            "goal": goal,
            "goals": list(AlternativeGoal),
            "tone": _pivot_tone(state.result),
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
        return "You're out of free AI calls for today — your account shows when they reset."
    if isinstance(exc, RateLimitExceeded):
        return "That's a lot of checks at once. Wait a minute, then try again."
    if isinstance(exc, LLMRejectedError):
        return str(exc)
    if isinstance(exc, LLMInvocationError):
        return "The model couldn't finish that step. Try again in a moment."
    return f"No AI provider is available for this: {exc}"
