from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.agents.dish_lookup import DishLookupAgent
from app.agents.recipe import RecipeAgent
from app.core.ratelimit import limiter, llm_rate_limit
from app.dependencies import (
    build_dish_lookup_agent,
    build_recipe_agent,
    get_dish_lookup_service,
    get_meal_service,
    get_request_llm_config,
)
from app.enums import MealType
from app.llm.request import RequestLLM
from app.schemas.meal import (
    AdaptedDish,
    DishAlternativesRequest,
    DishAlternativesResponse,
    DishAssessmentRequest,
    DishAssessmentResponse,
    DishLookupRequest,
    DishRewriteRequest,
    IngredientProposalResponse,
    LookupRecipeRequest,
    PublicMealDetail,
    PublicMealPage,
    RecipeGeneration,
)
from app.services.dish_lookup_service import DishLookupService
from app.services.meal_service import MealService, public_card, public_detail

router = APIRouter(prefix="/api/v1/meals", tags=["meals"])

# A browse read shifts only when an admin approves or removes a meal, so a short cache
# absorbs a burst of readers without serving a stale pool for long. Like the daily board,
# this caching (not a rate limit) is what bounds the load of a public, unauthenticated read.
_BROWSE_MAX_AGE = 60


@router.get("", response_model=PublicMealPage)
async def list_curated_meals(
    response: Response,
    meal_type: MealType | None = Query(default=None, description="Filter to one meal type."),
    limit: int = Query(default=24, ge=1, le=100, description="Maximum meals to return."),
    offset: int = Query(default=0, ge=0, description="How many meals to skip."),
    service: MealService = Depends(get_meal_service),
) -> PublicMealPage:
    """One page of approved curated meals for the public browse, newest first, plus a total.

    A plain read of the human-approved pool: no LLM call and no auth, since every row is
    verified-safe by construction and signed off by an admin. Cards are lean (the recipe
    and trace load from the detail endpoint on click), and the ``total`` lets the page
    page through the pool. A short cache (like the daily board) absorbs bursts of readers.
    """
    rows, total = await service.list_approved(meal_type=meal_type, limit=limit, offset=offset)
    response.headers["Cache-Control"] = f"public, max-age={_BROWSE_MAX_AGE}"
    return PublicMealPage(items=[public_card(row) for row in rows], total=total)


@router.get("/{meal_id}", response_model=PublicMealDetail)
async def get_curated_meal(
    meal_id: UUID,
    response: Response,
    service: MealService = Depends(get_meal_service),
) -> PublicMealDetail:
    """One approved meal in full, for the deep-linked detail; 404 when it is not public.

    A pending, rejected, or unknown id is indistinguishable here by design: an
    unapproved meal must never surface to a visitor, so all three read as not found.
    """
    row = await service.get_approved(meal_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal not found.")
    response.headers["Cache-Control"] = f"public, max-age={_BROWSE_MAX_AGE}"
    return public_detail(row)


@router.post("/propose", response_model=IngredientProposalResponse)
@limiter.limit(llm_rate_limit)
async def propose_ingredients(
    request: Request,
    payload: DishLookupRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
    resolved: RequestLLM = Depends(get_request_llm_config),
    lookup: DishLookupService = Depends(get_dish_lookup_service),
) -> IngredientProposalResponse:
    """Step 1 — what the model thinks is in the dish, for the caller to correct."""
    return await lookup.propose(payload, agent=agent, resolved=resolved)


@router.post("/assess", response_model=DishAssessmentResponse)
@limiter.limit(llm_rate_limit)
async def assess_dish(
    request: Request,
    payload: DishAssessmentRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
    resolved: RequestLLM = Depends(get_request_llm_config),
    lookup: DishLookupService = Depends(get_dish_lookup_service),
) -> DishAssessmentResponse:
    """Step 2 — the verdict for the confirmed ingredient list."""
    return await lookup.assess(payload, agent=agent, resolved=resolved)


@router.post("/adapt", response_model=AdaptedDish)
@limiter.limit(llm_rate_limit)
async def adapt_dish(
    request: Request,
    payload: DishRewriteRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
    resolved: RequestLLM = Depends(get_request_llm_config),
    lookup: DishLookupService = Depends(get_dish_lookup_service),
) -> AdaptedDish:
    """A version of the dish its ingredient list can support, or why there is none.

    The assessment behind it is recomputed from the same two fields rather than
    accepted from the caller, and is dropped here: this endpoint answers "what can
    I cook instead", and a client that wants the verdict too asks ``/assess``,
    which serves the very row this call just wrote.
    """
    _, adapted = await lookup.adapt(payload, agent=agent, resolved=resolved)
    return adapted


@router.post("/recipe", response_model=RecipeGeneration)
@limiter.limit(llm_rate_limit)
async def generate_lookup_recipe(
    request: Request,
    payload: LookupRecipeRequest,
    agent: RecipeAgent = Depends(build_recipe_agent),
    resolved: RequestLLM = Depends(get_request_llm_config),
    lookup: DishLookupService = Depends(get_dish_lookup_service),
) -> RecipeGeneration:
    """Write a recipe for an assessed dish straight off the result card."""
    return await lookup.recipe(payload, agent=agent, resolved=resolved)


@router.post("/alternatives", response_model=DishAlternativesResponse)
@limiter.limit(llm_rate_limit)
async def suggest_alternatives(
    request: Request,
    payload: DishAlternativesRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
    resolved: RequestLLM = Depends(get_request_llm_config),
    lookup: DishLookupService = Depends(get_dish_lookup_service),
) -> DishAlternativesResponse:
    """Step 3 — other dishes for a goal when this one cannot be rescued."""
    return await lookup.alternatives(payload, agent=agent, resolved=resolved)
