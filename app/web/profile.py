"""The signed-in visitor's shelf: saved meals, one saved copy, and its edits.

Saving, editing, the lazy recipe, and removal all run through the JSON API's own
handlers, so the per-user cap, the approval and reveal gates, the shared-tier
charge, and the write rate limits keep exactly one implementation. These routes
turn a form into that call and its refusal into page copy.

The edit form is plain HTML with no editor behind it: ingredients are one per
line as ``name | category``, and the recipe is one step per line. Both are read
back and handed to the same schema the JSON API validates, so a page edit can
only store what an API edit could.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from slowapi.errors import RateLimitExceeded

from app.dependencies import (
    build_recipe_agent,
    get_daily_service,
    get_ingredient_service,
    get_meal_service,
    get_request_llm_config,
    get_saved_meal_service,
)
from app.enums import MealType, SavedMealTag, SaveSource
from app.llm.errors import LLMError, LLMInvocationError, LLMRejectedError
from app.llm.request import RequestLLM
from app.models import SavedMeal
from app.models.user import User
from app.schemas.saved import SaveByReference, SavedMealDetail, SavedMealUpdate
from app.services.daily_service import DailyService
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService
from app.services.quota_service import QuotaExceededError
from app.services.saved_meal_service import (
    SavedMealNotFound,
    SavedMealService,
    SaveLimitReached,
    saved_card,
    saved_detail,
)
from app.web.deps import (
    INGREDIENT_SEPARATOR,
    ingredient_lines,
    parse_ingredient_lines,
    require_user,
    safe_redirect,
    templates,
)

router = APIRouter(prefix="/profile")

# The shelf filter for dish-check saves. An assessed dish has no meal slot, so a
# plain meal-type filter would strand it.
LOOKUP_FILTER = "lookup"
_FILTERS = {meal_type.value for meal_type in MealType} | {LOOKUP_FILTER}

# Failures the recipe button can hit that are worth saying on the page. A 404
# (the row was removed while the model wrote) is left to propagate as one.
_RECIPE_FAILURES = (RateLimitExceeded, QuotaExceededError, LLMError)

_EDIT_MESSAGES = {
    "name": "Give the meal a name.",
    "description": "Write a line describing the meal.",
    "ingredients": "List at least one ingredient.",
    "tags": "Pick tags from the list only.",
}


@router.get("", response_class=HTMLResponse)
async def shelf(
    request: Request,
    filter_by: str = Query(default="", alias="filter", description="A meal slot, or 'lookup'."),
    user: User = Depends(require_user),
    service: SavedMealService = Depends(get_saved_meal_service),
) -> HTMLResponse:
    """Every meal the visitor saved, newest first, optionally narrowed to one kind."""
    return await _shelf_page(request, user, service, selected=filter_by)


@router.post("/meals")
async def save_meal(
    request: Request,
    source: Literal[SaveSource.CURATED, SaveSource.DAILY] = Form(),
    source_id: UUID = Form(),
    back_url: str = Form(default="/"),
    user: User = Depends(require_user),
    service: SavedMealService = Depends(get_saved_meal_service),
    meal_service: MealService = Depends(get_meal_service),
    daily_service: DailyService = Depends(get_daily_service),
) -> Response:
    """Save a curated meal or a revealed daily slot, then return where it was saved from."""
    try:
        await service.save(
            user.id,
            SaveByReference(source=source, source_id=source_id),
            meals=meal_service,
            daily=daily_service,
        )
    except SaveLimitReached as exc:
        # The only refusal a save from a public page can hit is the per-user cap,
        # and the shelf is where it gets fixed.
        return await _shelf_page(request, user, service, error=str(exc))
    except SavedMealNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Meal not found."
        ) from exc
    return _back_to(back_url)


@router.get("/meals/{save_id}", response_class=HTMLResponse)
async def saved_copy(
    request: Request,
    save_id: UUID,
    user: User = Depends(require_user),
    service: SavedMealService = Depends(get_saved_meal_service),
) -> HTMLResponse:
    """One saved copy, as the form that edits it. Someone else's id reads as missing."""
    return _meal_page(request, saved_detail(await _owned(service, user, save_id)))


@router.post("/meals/{save_id}")
async def edit_saved_meal(
    request: Request,
    save_id: UUID,
    name: str = Form(),
    description: str = Form(),
    ingredients: str = Form(),
    recipe: str = Form(default=""),
    tags: list[str] = Form(default=[]),
    user: User = Depends(require_user),
    service: SavedMealService = Depends(get_saved_meal_service),
) -> Response:
    """Apply an edit to the visitor's own copy, which drops its verified badge."""
    row = await _owned(service, user, save_id)
    submitted: dict[str, object] = {
        "name": name,
        "description": description,
        "ingredients": ingredients,
        "recipe": recipe,
        "tags": tags,
    }
    try:
        # Validated rather than constructed: the fields are raw textarea text, and
        # the schema's normalizers are what turn them into a storable meal.
        payload = SavedMealUpdate.model_validate(
            {
                "name": name,
                "description": description,
                "ingredients": parse_ingredient_lines(ingredients),
                "recipe": recipe.splitlines(),
                "tags": tags,
            }
        )
    except ValidationError as exc:
        return _meal_page(request, saved_detail(row), form=submitted, error=_edit_failure(exc))
    await service.update(row, payload)
    return _back_to(f"/profile/meals/{save_id}")


@router.post("/meals/{save_id}/recipe")
async def write_recipe(
    request: Request,
    save_id: UUID,
    user: User = Depends(require_user),
    service: SavedMealService = Depends(get_saved_meal_service),
    ingredients: IngredientService = Depends(get_ingredient_service),
    resolved: RequestLLM = Depends(get_request_llm_config),
) -> Response:
    """Write a recipe for a saved copy that has none. One model call, ever, per copy.

    The agent is built here rather than injected so an unresolvable provider is
    page copy: raised from a dependency it would reach the browser as the API's
    JSON error body instead.
    """
    row = await _owned(service, user, save_id)
    try:
        agent = build_recipe_agent(resolved, ingredients)
        await service.generate_recipe(user.id, save_id, agent=agent, resolved=resolved)
    except _RECIPE_FAILURES as exc:
        return _meal_page(request, saved_detail(row), error=_recipe_failure(exc))
    return _back_to(f"/profile/meals/{save_id}")


@router.post("/meals/{save_id}/delete")
async def remove_saved_meal(
    request: Request,
    save_id: UUID,
    back_url: str = Form(default="/profile"),
    user: User = Depends(require_user),
    service: SavedMealService = Depends(get_saved_meal_service),
) -> Response:
    """Remove a saved copy, then return where it was removed from."""
    await service.delete(await _owned(service, user, save_id))
    return _back_to(back_url, fallback="/profile")


async def _owned(service: SavedMealService, user: User, save_id: UUID) -> SavedMeal:
    """The visitor's own saved row; anyone else's id reads as missing, never forbidden."""
    row = await service.get(user.id, save_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That meal is not in your saved list."
        )
    return row


async def _shelf_page(
    request: Request,
    user: User,
    service: SavedMealService,
    *,
    selected: str = "",
    error: str | None = None,
) -> HTMLResponse:
    """Render the shelf, narrowed to the selected filter."""
    rows = await service.list_for(user.id)
    selected = selected if selected in _FILTERS else ""
    return _private(
        templates.TemplateResponse(
            request,
            "profile.html",
            {
                "meals": [saved_card(row) for row in _narrow(rows, selected)],
                "total": len(rows),
                "selected": selected,
                "meal_types": list(MealType),
                "lookup_filter": LOOKUP_FILTER,
                "error": error,
            },
        )
    )


def _meal_page(
    request: Request,
    meal: SavedMealDetail,
    *,
    form: dict[str, object] | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Render one saved copy and the form that edits it.

    ``form`` carries back what was submitted when an edit failed, so a rejected
    edit is corrected rather than retyped.
    """
    return _private(
        templates.TemplateResponse(
            request,
            "saved_meal.html",
            {
                "meal": meal,
                "form": form or _form_fields(meal),
                "error": error,
                "saved_tags": list(SavedMealTag),
                "separator": INGREDIENT_SEPARATOR,
            },
        )
    )


def _private(response: HTMLResponse) -> HTMLResponse:
    """Mark a page as personal, so nothing between here and the browser keeps it."""
    response.headers["Cache-Control"] = "no-store"
    return response


def _narrow(rows: list[SavedMeal], selected: str) -> list[SavedMeal]:
    """The saves matching a filter: one meal slot, dish checks, or everything."""
    if selected == LOOKUP_FILTER:
        return [row for row in rows if row.source is SaveSource.LOOKUP]
    if selected:
        # A dish check has no slot, so its null meal_type simply matches nothing.
        return [row for row in rows if row.meal_type == selected]
    return rows


def _back_to(target: str, fallback: str = "/") -> RedirectResponse:
    """Redirect after a write, so a reload cannot repeat it."""
    return RedirectResponse(safe_redirect(target, fallback), status_code=status.HTTP_303_SEE_OTHER)


def _form_fields(meal: SavedMealDetail) -> dict[str, object]:
    """The saved copy as the edit form's text fields."""
    return {
        "name": meal.name,
        "description": meal.description,
        "ingredients": ingredient_lines(meal.ingredients),
        "recipe": "\n".join(meal.recipe or []),
        "tags": meal.tags,
    }


def _edit_failure(exc: ValidationError) -> str:
    """Plain copy for the first thing wrong with a submitted edit."""
    location = exc.errors()[0]["loc"]
    field = str(location[0]) if location else ""
    return _EDIT_MESSAGES.get(field, "That edit couldn't be saved. Check the fields and retry.")


def _recipe_failure(exc: BaseException) -> str:
    """Plain copy for a recipe the app could not write."""
    if isinstance(exc, QuotaExceededError):
        return "You're out of free AI calls for today. Your account page shows when they reset."
    if isinstance(exc, RateLimitExceeded):
        return "That's a lot of recipes at once. Wait a minute, then try again."
    if isinstance(exc, LLMRejectedError):
        return str(exc)
    if isinstance(exc, LLMInvocationError):
        return "The model couldn't write that recipe. Try again in a moment."
    return f"No AI provider is available for this: {exc}"
