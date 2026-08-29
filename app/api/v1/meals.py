from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.agents.dish_lookup import DishLookupAgent
from app.agents.recipe import RecipeAgent
from app.config import settings
from app.core.ratelimit import limiter, llm_rate_limit
from app.dependencies import (
    RequestLLM,
    build_dish_lookup_agent,
    build_recipe_agent,
    get_lookup_cache_service,
    get_meal_service,
    get_request_llm_config,
)
from app.enums import MealType
from app.schemas.meal import (
    CautionedIngredient,
    DishAlternativesRequest,
    DishAlternativesResponse,
    DishAssessmentRequest,
    DishAssessmentResponse,
    DishLookupRequest,
    IngredientProposalResponse,
    LookupRecipeRequest,
    ProposedIngredient,
    PublicMealDetail,
    PublicMealPage,
    RecipeGeneration,
)
from app.services.lookup_cache_service import LookupCacheService
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


def _cache_writes_allowed(resolved: RequestLLM) -> bool:
    """Whether this request's output may enter the shared lookup cache.

    On a public deployment only the operator-pinned shared tier writes: a BYO
    model is untrusted quality (and, via any endpoint-controllable provider,
    untrusted content), and must not populate state every visitor reads. A
    non-public deployment is one trust domain, so everything caches.
    """
    return resolved.shared or not settings.public_deployment


@router.post("/propose", response_model=IngredientProposalResponse)
@limiter.limit(llm_rate_limit)
async def propose_ingredients(
    request: Request,
    payload: DishLookupRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
    resolved: RequestLLM = Depends(get_request_llm_config),
    cache: LookupCacheService = Depends(get_lookup_cache_service),
) -> IngredientProposalResponse:
    # The cache is read before charging: a hit costs no model call, so it must
    # not consume shared-tier quota (the rate limit still bounds it).
    cached = await cache.get_proposal(payload.dish)
    if cached is not None:
        # Release the unspent shared-tier charge so the leak backstop does not
        # bill a free answer as a forgotten charge.
        resolved.waive()
        return cached
    # The same instance the agent was built from (FastAPI caches the dependency
    # per request); charging here, at the model-call boundary, is the contract.
    await resolved.charge()
    response = await agent.propose(dish=payload.dish)
    if _cache_writes_allowed(resolved):
        await cache.store_proposal(response)
    return response


@router.post("/assess", response_model=DishAssessmentResponse)
@limiter.limit(llm_rate_limit)
async def assess_dish(
    request: Request,
    payload: DishAssessmentRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
    resolved: RequestLLM = Depends(get_request_llm_config),
    cache: LookupCacheService = Depends(get_lookup_cache_service),
) -> DishAssessmentResponse:
    # A hit is only served while its grounding fingerprint still matches the
    # live index — no model call either way; see the service.
    cached = await cache.get_assessment(payload.dish, payload.ingredients)
    if cached is not None:
        resolved.waive()
        return cached
    await resolved.charge()
    response = await agent.assess(dish=payload.dish, ingredients=payload.ingredients)
    if _cache_writes_allowed(resolved):
        await cache.store_assessment(payload.dish, payload.ingredients, response)
    return response


@router.post("/recipe", response_model=RecipeGeneration)
@limiter.limit(llm_rate_limit)
async def generate_lookup_recipe(
    request: Request,
    payload: LookupRecipeRequest,
    agent: RecipeAgent = Depends(build_recipe_agent),
    resolved: RequestLLM = Depends(get_request_llm_config),
) -> RecipeGeneration:
    """Write a recipe for an assessed dish straight off the result card.

    Nothing is persisted: the result is not a saved meal (yet), so the steps
    live in the client until a save carries them along. The payload is
    client-asserted like a lookup save; the agent's own scan of the drafted
    steps against the index is the guardrail that matters.
    """
    await resolved.charge()
    return await agent.run(
        name=payload.dish,
        description=payload.description,
        ingredients=[
            ProposedIngredient(name=item.name, category=item.category)
            for item in payload.ingredients
        ],
        cautions=[
            CautionedIngredient(name=item.ingredient, note=item.note) for item in payload.advisories
        ],
    )


@router.post("/alternatives", response_model=DishAlternativesResponse)
@limiter.limit(llm_rate_limit)
async def suggest_alternatives(
    request: Request,
    payload: DishAlternativesRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
    resolved: RequestLLM = Depends(get_request_llm_config),
) -> DishAlternativesResponse:
    await resolved.charge()
    # Both ingredient lists are client-asserted: they only steer the suggestion
    # prompt, and every picked suggestion is fully re-vetted via propose/assess.
    return await agent.alternatives(
        dish=payload.dish,
        goal=payload.goal,
        avoid_ingredients=payload.avoid_ingredients,
        prefer_ingredients=payload.prefer_ingredients,
    )
