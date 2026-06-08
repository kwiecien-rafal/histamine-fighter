from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dish_lookup import DishLookupAgent
from app.db.session import get_session
from app.llm.config import LLMRequestConfig
from app.llm.langchain_factory import build_chat_model
from app.services.ingredient_service import IngredientService


def get_ingredient_service(session: AsyncSession = Depends(get_session)) -> IngredientService:
    return IngredientService(session)


def build_dish_lookup_agent(
    request: Request,
    service: IngredientService = Depends(get_ingredient_service),
) -> DishLookupAgent:
    """Wire a request-scoped dish-lookup agent: chat model + DB-backed index.

    ``build_chat_model`` resolves the provider from the request headers and may
    raise the LLM domain errors, which the API boundary maps to status codes.
    """
    chat = build_chat_model(LLMRequestConfig.from_headers(request))
    return DishLookupAgent(chat=chat, service=service)
