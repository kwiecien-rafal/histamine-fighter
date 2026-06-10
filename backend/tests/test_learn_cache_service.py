"""Tests for the Learn answer cache (DB-backed TTL cache)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LearnQueryCache
from app.schemas.learn import Citation, LearnResponse
from app.services.learn_cache_service import LearnCacheService


def _response(
    question: str, *, answer: str = "Grounded answer.", model: str = "test/model"
) -> LearnResponse:
    return LearnResponse(
        question=question,
        answer=answer,
        grounded=True,
        citations=[Citation(title="DAO", source="SIGHI", slug="dao")],
        model=model,
    )


async def test_roundtrip_echoes_the_callers_question(session: AsyncSession) -> None:
    cache = LearnCacheService(session)
    await cache.put("What is DAO?", _response("What is DAO?"))

    hit = await cache.get("what is DAO", "test/model")

    assert hit is not None
    assert hit.answer == "Grounded answer."
    assert (
        hit.question == "what is DAO"
    )  # the current request's phrasing, not the stored one
    assert [citation.slug for citation in hit.citations] == ["dao"]


async def test_normalization_shares_one_key(session: AsyncSession) -> None:
    cache = LearnCacheService(session)
    await cache.put("  What   is histamine?! ", _response("What is histamine?"))

    assert await cache.get("what is histamine", "test/model") is not None


async def test_miss_for_a_different_model(session: AsyncSession) -> None:
    cache = LearnCacheService(session)
    await cache.put("What is DAO?", _response("What is DAO?", model="test/model"))

    assert await cache.get("What is DAO?", "other/model") is None


async def test_ungrounded_answers_are_not_cached(session: AsyncSession) -> None:
    cache = LearnCacheService(session)
    decline = LearnResponse(
        question="q", answer="no idea", grounded=False, citations=[], model="test/model"
    )
    await cache.put("q", decline)

    assert await cache.get("q", "test/model") is None


async def test_expired_entry_is_a_miss(session: AsyncSession) -> None:
    cache = LearnCacheService(session, ttl_days=0)
    await cache.put("What is DAO?", _response("What is DAO?"))

    assert await cache.get("What is DAO?", "test/model") is None


async def test_put_upserts_instead_of_duplicating(session: AsyncSession) -> None:
    cache = LearnCacheService(session)
    await cache.put("What is DAO?", _response("What is DAO?", answer="First."))
    await cache.put("what is dao", _response("what is dao", answer="Second."))

    hit = await cache.get("What is DAO?", "test/model")
    rows = (
        await session.execute(select(func.count()).select_from(LearnQueryCache))
    ).scalar_one()

    assert hit is not None
    assert hit.answer == "Second."
    assert rows == 1
