from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.llm.base import LLMClient
from app.llm.config import LLMRequestConfig
from app.llm.factory import build_llm_client
from app.services.ingredient_service import IngredientService


def llm_dependency(request: Request) -> LLMClient:
    return build_llm_client(LLMRequestConfig.from_headers(request))


def get_ingredient_service(session: AsyncSession = Depends(get_session)) -> IngredientService:
    return IngredientService(session)
