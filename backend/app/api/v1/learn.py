from fastapi import APIRouter, Depends, Request

from app.agents.learn import LearnAgent
from app.core.ratelimit import limiter, llm_rate_limit
from app.dependencies import (
    build_learn_agent,
    get_knowledge_service,
    get_learn_cache_service,
)
from app.schemas.learn import ArticleListResponse, LearnQuery, LearnResponse
from app.services.knowledge_service import KnowledgeService
from app.services.learn_cache_service import LearnCacheService

router = APIRouter(prefix="/api/v1/learn", tags=["learn"])


@router.post("/query", response_model=LearnResponse)
@limiter.limit(llm_rate_limit)
async def query_knowledge(
    request: Request,
    payload: LearnQuery,
    agent: LearnAgent = Depends(build_learn_agent),
    cache: LearnCacheService = Depends(get_learn_cache_service),
) -> LearnResponse:
    """Answer a histamine question from the curated knowledge base, with citations.

    The corpus is static between seeds, so grounded answers are served from the
    TTL cache (keyed by normalized question and model) before touching the LLM.
    """
    cached = await cache.get(payload.question, agent.model_name)
    if cached is not None:
        return cached
    response = await agent.run(question=payload.question)
    await cache.put(payload.question, response)
    return response


@router.get("/articles", response_model=ArticleListResponse)
async def list_articles(
    service: KnowledgeService = Depends(get_knowledge_service),
) -> ArticleListResponse:
    """List the knowledge documents available to the Learn hub."""
    return ArticleListResponse(articles=await service.topics())
