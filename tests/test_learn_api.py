"""API tests for /api/v1/learn/query: the cache short-circuit and the rate limit.

No LLM runs here. A cache hit must be served before the agent is invoked, and an
empty corpus makes the agent decline without calling the model — so any attempt
to reach a real model would surface as a connection failure, failing the test.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.ratelimit import limiter
from app.schemas.learn import Citation, LearnResponse
from app.services.learn_cache_service import LearnCacheService

_DEFAULT_MODEL = f"ollama/{settings.ollama_model}"


async def test_cached_answer_is_served_without_an_llm(
    client: AsyncClient, session: AsyncSession
) -> None:
    cached = LearnResponse(
        question="What is DAO?",
        answer="Diamine oxidase breaks down histamine.",
        grounded=True,
        citations=[Citation(title="DAO", source="SIGHI", slug="dao")],
        model=_DEFAULT_MODEL,
    )
    await LearnCacheService(session).put("What is DAO?", cached)
    await session.flush()

    response = await client.post("/api/v1/learn/query", json={"question": "what is dao"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Diamine oxidase breaks down histamine."
    assert body["question"] == "what is dao"
    assert body["model"] == _DEFAULT_MODEL


async def test_llm_endpoint_is_rate_limited(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    limiter.reset()
    limiter.enabled = True

    first = await client.post("/api/v1/learn/query", json={"question": "what is histamine"})
    second = await client.post("/api/v1/learn/query", json={"question": "what is histamine"})

    # Empty corpus: the agent declines without an LLM call, so the first request
    # succeeds; the second must be cut off by the limiter.
    assert first.status_code == 200
    assert first.json()["grounded"] is False
    assert second.status_code == 429
