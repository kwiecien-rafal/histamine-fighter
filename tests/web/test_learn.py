"""Page tests for the Learn hub's topic index."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.web.factories import add_knowledge_chunk


async def test_learn_groups_documents_by_topic(client: AsyncClient, session: AsyncSession) -> None:
    await add_knowledge_chunk(session, slug="dao", title="What DAO does", topic="Basics")
    await add_knowledge_chunk(session, slug="leftovers", title="Why leftovers bite", topic="Food")

    response = await client.get("/learn")

    assert response.status_code == 200
    assert "Basics" in response.text
    assert "What DAO does" in response.text
    assert "Food" in response.text
    assert "Why leftovers bite" in response.text


async def test_learn_says_so_when_the_corpus_is_empty(client: AsyncClient) -> None:
    response = await client.get("/learn")

    assert response.status_code == 200
    assert "The knowledge library is empty" in response.text
