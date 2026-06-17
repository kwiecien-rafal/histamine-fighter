from fastapi import APIRouter, Depends, Request

from app.agents.dish_lookup import DishLookupAgent
from app.core.ratelimit import limiter, llm_rate_limit
from app.dependencies import build_dish_lookup_agent
from app.schemas.meal import (
    DishAlternativesRequest,
    DishAlternativesResponse,
    DishAssessmentRequest,
    DishAssessmentResponse,
    DishLookupRequest,
    IngredientProposalResponse,
)

router = APIRouter(prefix="/api/v1/meals", tags=["meals"])


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
