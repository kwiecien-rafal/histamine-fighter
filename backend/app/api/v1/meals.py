from fastapi import APIRouter, Depends

from app.agents.dish_lookup import DishLookupAgent
from app.dependencies import llm_dependency
from app.llm.base import LLMClient
from app.schemas.meal import DishLookupRequest, DishLookupResponse

router = APIRouter(prefix="/api/v1/meals", tags=["meals"])


@router.post("/lookup", response_model=DishLookupResponse)
async def lookup_dish(
    payload: DishLookupRequest,
    llm: LLMClient = Depends(llm_dependency),
) -> DishLookupResponse:
    agent = DishLookupAgent(llm=llm)
    result = await agent.run(dish=payload.dish)
    return DishLookupResponse(**result)
