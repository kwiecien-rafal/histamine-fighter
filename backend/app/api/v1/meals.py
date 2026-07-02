from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.agents.dish_lookup import DishLookupAgent
from app.core.ratelimit import limiter, llm_rate_limit
from app.dependencies import build_dish_lookup_agent, get_meal_service
from app.enums import MealType
from app.models import CuratedMeal
from app.schemas.meal import (
    DishAlternativesRequest,
    DishAlternativesResponse,
    DishAssessmentRequest,
    DishAssessmentResponse,
    DishLookupRequest,
    IngredientProposalResponse,
    ProposedIngredient,
    PublicMealCard,
    PublicMealDetail,
    PublicMealPage,
    TraceEvent,
    public_trace,
)
from app.services.meal_service import MealService

router = APIRouter(prefix="/api/v1/meals", tags=["meals"])

# A browse read shifts only when an admin approves or removes a meal, so a short cache
# absorbs a burst of readers without serving a stale pool for long. Like the daily board,
# this caching (not a rate limit) is what bounds the load of a public, unauthenticated read.
_BROWSE_MAX_AGE = 60


def _public_events(row: CuratedMeal) -> list[TraceEvent]:
    """The row's reasoning trace, validated and filtered to the steps a visitor may see."""
    return public_trace([TraceEvent.model_validate(event) for event in row.reasoning_trace])


def _to_list_card(row: CuratedMeal) -> PublicMealCard:
    """Shape an approved row into its lean browse-list card (no recipe or trace shipped)."""
    return PublicMealCard(
        id=row.id,
        meal_type=row.meal_type,
        model=row.model,
        name=row.name,
        description=row.description,
        tags=list(row.tags),
        has_recipe=bool(row.recipe),
        has_trace=bool(_public_events(row)),
    )


def _to_detail(row: CuratedMeal) -> PublicMealDetail:
    """Shape an approved row into its full public detail, trace filtered."""
    return PublicMealDetail(
        id=row.id,
        meal_type=row.meal_type,
        model=row.model,
        name=row.name,
        description=row.description,
        ingredients=[ProposedIngredient.model_validate(item) for item in row.ingredients],
        recipe=row.recipe,
        tags=list(row.tags),
        trace=_public_events(row),
    )


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
    return PublicMealPage(items=[_to_list_card(row) for row in rows], total=total)


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
    return _to_detail(row)


@router.post("/propose", response_model=IngredientProposalResponse)
@limiter.limit(llm_rate_limit)
async def propose_ingredients(
    request: Request,
    payload: DishLookupRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
) -> IngredientProposalResponse:
    return await agent.propose(dish=payload.dish)


@router.post("/assess", response_model=DishAssessmentResponse)
@limiter.limit(llm_rate_limit)
async def assess_dish(
    request: Request,
    payload: DishAssessmentRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
) -> DishAssessmentResponse:
    return await agent.assess(dish=payload.dish, ingredients=payload.ingredients)


@router.post("/alternatives", response_model=DishAlternativesResponse)
@limiter.limit(llm_rate_limit)
async def suggest_alternatives(
    request: Request,
    payload: DishAlternativesRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
) -> DishAlternativesResponse:
    # Both ingredient lists are client-asserted: they only steer the suggestion
    # prompt, and every picked suggestion is fully re-vetted via propose/assess.
    return await agent.alternatives(
        dish=payload.dish,
        goal=payload.goal,
        avoid_ingredients=payload.avoid_ingredients,
        prefer_ingredients=payload.prefer_ingredients,
    )
