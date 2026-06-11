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


async def _lookup(
    session: AsyncSession, ingredient: str, category: str | None = None
) -> dict[str, Any]:
    tool = build_dish_lookup_tools(IngredientService(session))[0]
    result: dict[str, Any] = await tool.ainvoke({"ingredient": ingredient, "category": category})
    return result


async def test_tool_reports_compatibility_for_a_known_ingredient(
    session: AsyncSession,
) -> None:
    session.add(
        _ingredient("Tomato", compatibility=Compatibility.INCOMPATIBLE, category="vegetable")
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


# --- the category fallback --------------------------------------------------------


def _hard_cheese() -> HistamineIngredient:
    return _ingredient(
        "Hard Cheese",
        compatibility=Compatibility.POORLY_TOLERATED,
        category="cheese",
        is_category=True,
        aliases=["aged cheese", "aged hard cheese"],
    )


async def test_tool_falls_back_to_the_category_on_an_ingredient_miss(
    session: AsyncSession,
) -> None:
    # "parmesan" is not indexed, but its category resolves to the umbrella row —
    # the structural fix for foods the index only knows as a group.
    session.add(_hard_cheese())
    await session.flush()

    result = await _lookup(session, "parmesan", category="aged hard cheese")
    assert result["found"] is True
    assert result["matched_on"] == "category"
    assert [(c["name"], c["compatibility"]) for c in result["candidates"]] == [
        ("Hard Cheese", "poorly_tolerated")
    ]


async def test_tool_ignores_the_category_when_the_ingredient_is_indexed(
    session: AsyncSession,
) -> None:
    # The specific row is authoritative: mozzarella's own well-tolerated entry
    # must not be poisoned by its risky category neighbours.
    session.add(_ingredient("Mozzarella", compatibility=Compatibility.WELL_TOLERATED))
    session.add(_hard_cheese())
    await session.flush()

    result = await _lookup(session, "mozzarella", category="aged hard cheese")
    assert result["matched_on"] == "ingredient"
    assert [c["name"] for c in result["candidates"]] == ["Mozzarella"]


async def test_tool_reports_a_double_miss_as_not_found(session: AsyncSession) -> None:
    session.add(_hard_cheese())
    await session.flush()

    result = await _lookup(session, "dragonfruit", category="exotic fruit")
    assert result["found"] is False
    assert result["matched_on"] is None
    assert result["candidates"] == []


async def test_tool_treats_a_blank_category_as_absent(session: AsyncSession) -> None:
    result = await _lookup(session, "dragonfruit", category="   ")
    assert result["found"] is False
    assert result["matched_on"] is None


async def test_tool_contains_a_category_lookup_error_as_unknown(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A blip in the fallback path must degrade to "unknown" like the primary path.
    service = IngredientService(session)

    async def _raise(_category: str) -> Sequence[IngredientMatch]:
        raise SQLAlchemyError("connection lost")

    monkeypatch.setattr(service, "find_category_candidates", _raise)
    tool = build_dish_lookup_tools(service)[0]

    result = await tool.ainvoke({"ingredient": "parmesan", "category": "aged hard cheese"})
    assert result["found"] is False
    assert result["error"]
    assert result["candidates"] == []


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
