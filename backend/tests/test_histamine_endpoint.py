"""Tests for the public ingredient lookup endpoint."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import Compatibility
from app.models import HistamineIngredient


def _ingredient(name: str, **kwargs: object) -> HistamineIngredient:
    return HistamineIngredient(name=name, sources=["test source"], **kwargs)


async def test_lookup_returns_candidates(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add(_ingredient("Tomato", compatibility=Compatibility.INCOMPATIBLE))
    await session.flush()

    resp = await client.get("/api/v1/histamine/ingredient", params={"name": "tomatos"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["ambiguous"] is False
    top = body["candidates"][0]
    assert top["name"] == "Tomato"
    assert top["match_type"] == "fuzzy"
    assert top["compatibility"] == "incompatible"


async def test_ambiguous_query_is_flagged(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add(_ingredient("Egg Yolk", compatibility=Compatibility.WELL_TOLERATED))
    session.add(_ingredient("Egg White", compatibility=Compatibility.INCOMPATIBLE))
    await session.flush()

    resp = await client.get("/api/v1/histamine/ingredient", params={"name": "egg"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["ambiguous"] is True
    verdicts = {c["compatibility"] for c in body["candidates"]}
    assert {"well_tolerated", "incompatible"} <= verdicts


async def test_unrated_ingredient_reports_unknown_not_null(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add(_ingredient("Bamboo Shoots"))  # no compatibility -> NULL in the column
    await session.flush()

    resp = await client.get(
        "/api/v1/histamine/ingredient", params={"name": "bamboo shoots"}
    )

    assert resp.status_code == 200
    top = resp.json()["candidates"][0]
    assert top["compatibility"] == "unknown"


async def test_unknown_returns_empty_candidates(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/histamine/ingredient", params={"name": "qwertyzzz"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["ambiguous"] is False
    assert body["candidates"] == []


async def test_blank_name_is_rejected(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/histamine/ingredient", params={"name": ""})
    assert resp.status_code == 422
