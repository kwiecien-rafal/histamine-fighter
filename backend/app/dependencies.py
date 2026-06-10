from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dish_lookup import DishLookupAgent
from app.agents.learn import LearnAgent
from app.db.session import get_session
from app.embeddings import get_embedder
from app.llm.config import LLMRequestConfig
from app.llm.langchain_factory import build_chat_model
from app.services.ingredient_service import IngredientService
from app.services.knowledge_service import KnowledgeService
from app.services.learn_cache_service import LearnCacheService


def get_ingredient_service(
    session: AsyncSession = Depends(get_session),
) -> IngredientService:
    return IngredientService(session)


def get_knowledge_service(
    session: AsyncSession = Depends(get_session),
) -> KnowledgeService:
    # get_embedder returns the process-wide singleton; the service takes it by
    # constructor so a test can inject a deterministic stand-in instead.
    return KnowledgeService(session, get_embedder())


def get_learn_cache_service(
    session: AsyncSession = Depends(get_session),
) -> LearnCacheService:
    return LearnCacheService(session)


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


def build_learn_agent(
    request: Request,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> LearnAgent:
    """Wire a request-scoped Learn agent: chat model + vector knowledge retrieval.

    A higher temperature than the dish lookup: the answer is readable educational
    prose, and faithfulness is enforced by the retrieved context and the prompt,
    not by pinning the sampler.
    """
    chat = build_chat_model(LLMRequestConfig.from_headers(request), temperature=0.3)
    return LearnAgent(chat=chat, service=service)
