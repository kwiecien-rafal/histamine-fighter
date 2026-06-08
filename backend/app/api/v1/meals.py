from fastapi import APIRouter, Depends

from app.agents.dish_lookup import DishLookupAgent
from app.dependencies import build_dish_lookup_agent
from app.schemas.meal import DishLookupRequest, DishLookupResponse

router = APIRouter(prefix="/api/v1/meals", tags=["meals"])


@router.post("/lookup", response_model=DishLookupResponse)
async def lookup_dish(
    payload: DishLookupRequest,
    agent: DishLookupAgent = Depends(build_dish_lookup_agent),
) -> DishLookupResponse:
    return await agent.run(dish=payload.dish)
