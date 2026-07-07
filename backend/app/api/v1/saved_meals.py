"""Routes for the signed-in user's saved meals: list, save, edit, unsave.

Everything here requires a session cookie. Curated and daily saves are copied
server-side, gated exactly as the public reads gate them (approved, and revealed
for daily), and any miss answers 404 so probing ids confirms nothing about
unapproved content. Foreign save ids read as 404 for the same reason. Writes sit
behind their own per-IP rate limit, roomier than the auth one.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.agents.recipe import RecipeAgent
from app.config import settings
from app.core.ratelimit import limiter, llm_rate_limit, save_rate_limit
from app.dependencies import (
    RequestLLM,
    build_recipe_agent,
    get_current_user,
    get_daily_service,
    get_meal_service,
    get_request_llm_config,
    get_saved_meal_service,
)
from app.enums import ApprovalStatus, SaveSource
from app.models import SavedMeal
from app.models.user import User
from app.schemas.meal import CautionedIngredient, ProposedIngredient
from app.schemas.saved import (
    SavedMealCard,
    SavedMealDetail,
    SavedMealPage,
    SavedMealUpdate,
    SavedRecipeResponse,
    SaveFromLookup,
    SaveRequest,
)
from app.schemas.usage import LLMUsage
from app.services.daily_service import DailyService
from app.services.meal_service import MealService
from app.services.saved_meal_service import SavedMealService

router = APIRouter(prefix="/api/v1/me/meals", tags=["saved-meals"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal not found.")


def _to_card(row: SavedMeal) -> SavedMealCard:
    return SavedMealCard(
        id=row.id,
        source=row.source,
        source_key=row.source_key,
        meal_type=row.meal_type,
        name=row.name,
        description=row.description,
        tags=list(row.tags),
        verdict=row.verdict,
        edited_at=row.edited_at,
        created_at=row.created_at,
        has_recipe=bool(row.recipe),
    )


def _to_detail(row: SavedMeal) -> SavedMealDetail:
    return SavedMealDetail(
        **_to_card(row).model_dump(),
        ingredients=[ProposedIngredient.model_validate(item) for item in row.ingredients],
        recipe=row.recipe,
        cautioned_ingredients=[
            CautionedIngredient.model_validate(item) for item in row.cautioned_ingredients
        ],
        model=row.model,
        recipe_model=row.recipe_model,
    )


@router.get("", response_model=SavedMealPage)
async def list_saved_meals(
    response: Response,
    user: User = Depends(get_current_user),
    service: SavedMealService = Depends(get_saved_meal_service),
) -> SavedMealPage:
    """Every saved meal for the signed-in user, newest first."""
    response.headers["Cache-Control"] = "no-store"
    rows = await service.list_for(user.id)
    return SavedMealPage(items=[_to_card(row) for row in rows])


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
    return _to_detail(row)


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

    Idempotent per (source, source row): re-liking returns the earlier snapshot
    even if the source has changed since. A lookup save is keyed on the client's
    per-result ``lookup_id``, so each assessment result saves as its own row and
    only a retry of the same result is idempotent. The per-user cap answers 409;
    it is an abuse bound, not something a real collection should reach.
    """
    if isinstance(payload, SaveFromLookup):
        source_key = str(payload.lookup_id)
    else:
        source_key = str(payload.source_id)

    existing = await service.find(user.id, payload.source, source_key)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _to_detail(existing)

    if await service.count_for(user.id) >= settings.saved_meals_cap:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Save limit reached ({settings.saved_meals_cap}). Remove some first.",
        )

    if isinstance(payload, SaveFromLookup):
        row, created = await service.save_lookup(user.id, payload)
    elif payload.source is SaveSource.CURATED:
        meal = await meal_service.get_approved(payload.source_id)
        if meal is None:
            raise _not_found()
        row, created = await service.save_curated(user.id, meal)
    else:
        suggestion = await daily_service.get(payload.source_id)
        if (
            suggestion is None
            or suggestion.approval_status is not ApprovalStatus.APPROVED
            or datetime.now(UTC) < suggestion.reveal_at
        ):
            # Unknown, unapproved, and unrevealed are indistinguishable on purpose:
            # a saved id must not become a probe for tomorrow's board.
            raise _not_found()
        row, created = await service.save_daily(user.id, suggestion)

    if not created:
        response.status_code = status.HTTP_200_OK
    return _to_detail(row)


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
    """Write a recipe for a saved meal that has none, and persist it on the row.

    Lazy by design: recipes cost a model call, so one is only written when its
    owner asks, from the snapshot's current (possibly user-edited) ingredients.
    Idempotent: a row that already has a recipe returns unchanged, uncharged.
    """
    response.headers["Cache-Control"] = "no-store"
    row = await service.get(user.id, save_id)
    if row is None:
        raise _not_found()
    if row.recipe:
        # A recipe that came with the snapshot has no recipe_model of its own;
        # the save's producer is then the closest honest provenance.
        return SavedRecipeResponse(
            meal=_to_detail(row),
            recipe_model=row.recipe_model or row.model,
            usage=LLMUsage(),
        )

    await resolved.charge()
    generation = await agent.run(
        name=row.name,
        description=row.description,
        ingredients=[ProposedIngredient.model_validate(item) for item in row.ingredients],
        cautions=[CautionedIngredient.model_validate(item) for item in row.cautioned_ingredients],
    )
    saved = await service.set_recipe(user.id, save_id, generation.steps, generation.model)
    if saved is None:
        # The save was deleted while the model wrote; nothing was persisted, so
        # a response claiming a recipe exists would be a lie.
        raise _not_found()
    return SavedRecipeResponse(
        meal=_to_detail(saved),
        recipe_model=saved.recipe_model or saved.model,
        usage=generation.usage,
    )


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
    return _to_detail(await service.update(row, payload))


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
