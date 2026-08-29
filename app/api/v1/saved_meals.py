"""Routes for the signed-in user's saved meals: list, save, edit, unsave.

Everything here requires a session cookie. Curated and daily saves are copied
server-side, gated exactly as the public reads gate them (approved, and revealed
for daily), and any miss answers 404 so probing ids confirms nothing about
unapproved content. Foreign save ids read as 404 for the same reason. Writes sit
behind their own per-IP rate limit, roomier than the auth one.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.agents.recipe import RecipeAgent
from app.core.ratelimit import limiter, llm_rate_limit, save_rate_limit
from app.dependencies import (
    build_recipe_agent,
    get_current_user,
    get_daily_service,
    get_meal_service,
    get_request_llm_config,
    get_saved_meal_service,
)
from app.llm.request import RequestLLM
from app.models.user import User
from app.schemas.saved import (
    SavedMealDetail,
    SavedMealPage,
    SavedMealUpdate,
    SavedRecipeResponse,
    SaveRequest,
)
from app.services.daily_service import DailyService
from app.services.meal_service import MealService
from app.services.saved_meal_service import (
    SavedMealNotFound,
    SavedMealService,
    SaveLimitReached,
    saved_card,
    saved_detail,
)

router = APIRouter(prefix="/api/v1/me/meals", tags=["saved-meals"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal not found.")


@router.get("", response_model=SavedMealPage)
async def list_saved_meals(
    response: Response,
    user: User = Depends(get_current_user),
    service: SavedMealService = Depends(get_saved_meal_service),
) -> SavedMealPage:
    """Every saved meal for the signed-in user, newest first."""
    response.headers["Cache-Control"] = "no-store"
    rows = await service.list_for(user.id)
    return SavedMealPage(items=[saved_card(row) for row in rows])


@router.get("/{save_id}", response_model=SavedMealDetail)
async def get_saved_meal(
    save_id: UUID,
    response: Response,
    user: User = Depends(get_current_user),
    service: SavedMealService = Depends(get_saved_meal_service),
) -> SavedMealDetail:
    """One saved meal in full; someone else's id reads as 404, never 403."""
    response.headers["Cache-Control"] = "no-store"
    row = await service.get(user.id, save_id)
    if row is None:
        raise _not_found()
    return saved_detail(row)


@router.post("", response_model=SavedMealDetail, status_code=status.HTTP_201_CREATED)
@limiter.limit(save_rate_limit)
async def save_meal(
    request: Request,
    payload: SaveRequest,
    response: Response,
    user: User = Depends(get_current_user),
    service: SavedMealService = Depends(get_saved_meal_service),
    meal_service: MealService = Depends(get_meal_service),
    daily_service: DailyService = Depends(get_daily_service),
) -> SavedMealDetail:
    """Save a meal: 201 with the stored snapshot, or 200 with the existing one.

    Idempotent per (source, source row); the service owns that and the source gates.
    The per-user cap answers 409 — an abuse bound, not something a real collection
    should reach.
    """
    try:
        row, created = await service.save(user.id, payload, meals=meal_service, daily=daily_service)
    except SaveLimitReached as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SavedMealNotFound as exc:
        raise _not_found() from exc

    if not created:
        response.status_code = status.HTTP_200_OK
    return saved_detail(row)


@router.post("/{save_id}/recipe", response_model=SavedRecipeResponse)
@limiter.limit(llm_rate_limit)
async def generate_saved_recipe(
    request: Request,
    save_id: UUID,
    response: Response,
    user: User = Depends(get_current_user),
    service: SavedMealService = Depends(get_saved_meal_service),
    agent: RecipeAgent = Depends(build_recipe_agent),
    resolved: RequestLLM = Depends(get_request_llm_config),
) -> SavedRecipeResponse:
    """Write a recipe for a saved meal that has none, and persist it on the row."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.generate_recipe(user.id, save_id, agent=agent, resolved=resolved)
    except SavedMealNotFound as exc:
        raise _not_found() from exc


@router.patch("/{save_id}", response_model=SavedMealDetail)
@limiter.limit(save_rate_limit)
async def update_saved_meal(
    request: Request,
    save_id: UUID,
    payload: SavedMealUpdate,
    user: User = Depends(get_current_user),
    service: SavedMealService = Depends(get_saved_meal_service),
) -> SavedMealDetail:
    """Edit the user's own copy; marks it user-modified (the verified badge drops)."""
    row = await service.get(user.id, save_id)
    if row is None:
        raise _not_found()
    return saved_detail(await service.update(row, payload))


@router.delete("/{save_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(save_rate_limit)
async def unsave_meal(
    request: Request,
    save_id: UUID,
    user: User = Depends(get_current_user),
    service: SavedMealService = Depends(get_saved_meal_service),
) -> None:
    """Remove a saved meal; someone else's id reads as 404."""
    row = await service.get(user.id, save_id)
    if row is None:
        raise _not_found()
    await service.delete(row)
