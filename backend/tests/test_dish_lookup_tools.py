"""Tests for the dish-lookup agent's database-backed tools."""

from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import build_dish_lookup_tools
from app.enums import Compatibility, HistamineMechanism
from app.models import HistamineIngredient
from app.services.ingredient_service import IngredientMatch, IngredientService


def _ingredient(name: str, **kwargs: object) -> HistamineIngredient:
    return HistamineIngredient(name=name, sources=["test source"], **kwargs)


async def _lookup(session: AsyncSession, ingredient: str) -> dict[str, Any]:
    tool = build_dish_lookup_tools(IngredientService(session))[0]
    result: dict[str, Any] = await tool.ainvoke({"ingredient": ingredient})
    return result


async def test_tool_reports_compatibility_for_a_known_ingredient(
    session: AsyncSession,
) -> None:
    session.add(
        _ingredient(
            "Tomato", compatibility=Compatibility.INCOMPATIBLE, category="vegetable"
        )
    )
    await session.flush()

    result = await _lookup(session, "tomatos")  # fuzzy
    assert result["found"] is True
    assert result["ambiguous"] is False
    top = result["candidates"][0]
    assert top["name"] == "Tomato"
    assert top["compatibility"] == "incompatible"
    assert top["category"] == "vegetable"


async def test_tool_flags_unknown_ingredient_without_claiming_safe(
    session: AsyncSession,
) -> None:
    session.add(_ingredient("Tomato"))
    await session.flush()

    result = await _lookup(session, "qwertyzzz")
    assert result["found"] is False
    assert result["candidates"] == []
    assert result["ambiguous"] is False


async def test_tool_reports_unknown_compatibility_not_null(
    session: AsyncSession,
) -> None:
    session.add(_ingredient("Bamboo Shoots"))  # no compatibility -> NULL in the column
    await session.flush()

    result = await _lookup(session, "bamboo shoots")
    assert result["candidates"][0]["compatibility"] == "unknown"


async def test_tool_flags_ambiguous_ingredient(session: AsyncSession) -> None:
    session.add(_ingredient("Egg Yolk", compatibility=Compatibility.WELL_TOLERATED))
    session.add(_ingredient("Egg White", compatibility=Compatibility.INCOMPATIBLE))
    await session.flush()

    result = await _lookup(session, "egg")
    assert result["found"] is True
    assert result["ambiguous"] is True


async def test_tool_returns_mechanisms_to_ground_the_explanation(
    session: AsyncSession,
) -> None:
    # mechanisms are the curated "why"; without them the model invents reasons.
    session.add(
        _ingredient(
            "Parmesan",
            compatibility=Compatibility.INCOMPATIBLE,
            mechanisms=[
                HistamineMechanism.HIGH_HISTAMINE,
                HistamineMechanism.DAO_BLOCKER,
            ],
        )
    )
    await session.flush()

    result = await _lookup(session, "parmesan")
    assert result["candidates"][0]["mechanisms"] == ["high_histamine", "dao_blocker"]


async def test_tool_rejects_empty_input_with_a_signal(session: AsyncSession) -> None:
    result = await _lookup(session, "   ")
    assert result["found"] is False
    assert result["error"]
    assert result["candidates"] == []


async def test_tool_rejects_overlong_input_with_a_signal(session: AsyncSession) -> None:
    # The model could pass a whole dish or a hallucination; it gets a signal, not
    # silent junk, and the trigram scan is never run on an oversized string.
    result = await _lookup(session, "x" * 500)
    assert result["error"]
    assert result["candidates"] == []


async def test_tool_contains_a_database_error_as_unknown(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A blip looking up one ingredient must not abort the whole dish analysis: it
    # comes back as "unknown" so the agent can continue cautiously.
    service = IngredientService(session)

    async def _raise(_name: str) -> Sequence[IngredientMatch]:
        raise SQLAlchemyError("connection lost")

    monkeypatch.setattr(service, "find_candidates", _raise)
    tool = build_dish_lookup_tools(service)[0]

    result = await tool.ainvoke({"ingredient": "parmesan"})
    assert result["found"] is False
    assert result["error"]
    assert result["candidates"] == []
