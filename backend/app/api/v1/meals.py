from fastapi import APIRouter, Depends, Request

from app.agents.dish_lookup import DishLookupAgent
from app.core.ratelimit import limiter, llm_rate_limit
from app.dependencies import build_dish_lookup_agent
from app.schemas.meal import DishLookupRequest, DishLookupResponse

router = APIRouter(prefix="/api/v1/meals", tags=["meals"])


@router.post("/lookup", response_model=DishLookupResponse)
@limiter.limit(llm_rate_limit)
async def lookup_dish(
    request: Request,
    payload: DishLookupRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
) -> DishLookupResponse:
    return await agent.run(dish=payload.dish)
