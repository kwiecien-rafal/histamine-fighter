"""Tests for vector retrieval over the knowledge corpus (DB + fake embedder)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeChunk
from app.schemas.learn import MAX_QUESTION_LENGTH
from app.services.knowledge_service import KnowledgeService
from tests.fakes import FakeEmbedder


async def _add_chunk(
    session: AsyncSession,
    embedder: FakeEmbedder,
    *,
    slug: str,
    title: str,
    content: str,
    topic: str = "basics",
    index: int = 0,
) -> None:
    vector = (await embedder.embed_documents([content]))[0]
    session.add(
        KnowledgeChunk(
            slug=slug,
            title=title,
            source="test source",
            topic=topic,
            chunk_index=index,
            content=content,
            embedding=vector,
        )
    )


async def test_search_ranks_relevant_chunk_first(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _add_chunk(
        session,
        fake_embedder,
        slug="dao",
        title="DAO enzyme",
        content="diamine oxidase is the enzyme that breaks down histamine in the gut",
    )
    await _add_chunk(
        session,
        fake_embedder,
        slug="foods",
        title="Foods",
        content="aged cheese and cured meats are often high in histamine",
    )
    await session.flush()

    # Floor disabled: this test is about ranking, and the bag-of-words fake
    # produces lower similarities than the real model the default is tuned for.
    service = KnowledgeService(session, fake_embedder, min_similarity=0.0)
    results = await service.search("what enzyme breaks down histamine")

    assert results
    assert results[0].chunk.slug == "dao"
    assert results[0].similarity >= results[-1].similarity


async def test_empty_query_returns_nothing(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    assert await KnowledgeService(session, fake_embedder).search("   ") == []


async def test_oversized_query_raises(session: AsyncSession, fake_embedder: FakeEmbedder) -> None:
    """Too-long input is a caller error, not 'nothing relevant found'."""
    service = KnowledgeService(session, fake_embedder)
    with pytest.raises(ValueError, match="exceeds"):
        await service.search("x" * (MAX_QUESTION_LENGTH + 1))


async def test_non_positive_k_raises(session: AsyncSession, fake_embedder: FakeEmbedder) -> None:
    """k=0 must fail loudly, not silently fall back to the default."""
    service = KnowledgeService(session, fake_embedder)
    with pytest.raises(ValueError, match="k must be >= 1"):
        await service.search("histamine", k=0)
    with pytest.raises(ValueError, match="k must be >= 1"):
        await service.search("histamine", k=-3)


async def test_search_respects_k(session: AsyncSession, fake_embedder: FakeEmbedder) -> None:
    for i in range(4):
        await _add_chunk(
            session,
            fake_embedder,
            slug=f"d{i}",
            title=f"T{i}",
            content=f"histamine fact {i}",
        )
    await session.flush()

    service = KnowledgeService(session, fake_embedder, min_similarity=0.0)
    results = await service.search("histamine", k=2)

    assert len(results) == 2


async def test_off_topic_query_returns_nothing(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    """An unrelated query must yield no context, not the least-unrelated chunks."""
    await _add_chunk(
        session,
        fake_embedder,
        slug="foods",
        title="Foods",
        content="aged cheese and cured meats are often high in histamine",
    )
    await session.flush()

    service = KnowledgeService(session, fake_embedder, min_similarity=0.5)
    assert await service.search("how do I tune a guitar") == []


async def test_similarity_floor_keeps_only_relevant_chunks(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _add_chunk(
        session,
        fake_embedder,
        slug="dao",
        title="DAO enzyme",
        content="diamine oxidase breaks down histamine",
    )
    await _add_chunk(
        session,
        fake_embedder,
        slug="other",
        title="Other",
        content="completely unrelated words about carpentry and sailing",
    )
    await session.flush()

    service = KnowledgeService(session, fake_embedder, min_similarity=0.5)
    results = await service.search("diamine oxidase breaks down histamine")

    assert [match.chunk.slug for match in results] == ["dao"]


async def test_topics_lists_one_row_per_document(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _add_chunk(session, fake_embedder, slug="dao", title="DAO", content="x", index=0)
    await _add_chunk(session, fake_embedder, slug="dao", title="DAO", content="y", index=1)
    await _add_chunk(
        session, fake_embedder, slug="foods", title="Foods", content="z", topic="foods"
    )
    await session.flush()

    topics = await KnowledgeService(session, fake_embedder).topics()

    slugs = [article.slug for article in topics]
    assert slugs.count("dao") == 1
    assert set(slugs) == {"dao", "foods"}
