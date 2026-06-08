"""Tests for the grounded, tool-calling DishLookupAgent.

A scripted stand-in chat model replays tool-call turns and a final explanation,
while the real tool runs against the seeded test DB — so these exercise the loop,
the code-owned verdict, the swap grounding, and the grounding floor without any
network call.
"""

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dish_lookup import DishLookupAgent
from app.enums import Compatibility, SafetyLevel
from app.llm.errors import LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.models import HistamineIngredient
from app.schemas.meal import DishExplanation, Replacement
from app.services.ingredient_service import IngredientService


def _ingredient(name: str, **kwargs: object) -> HistamineIngredient:
    return HistamineIngredient(name=name, sources=["test source"], **kwargs)


def _tool_call(ingredient: str, call_id: str = "c1") -> dict[str, Any]:
    args = {"ingredient": ingredient}
    return {"name": "lookup_ingredient_safety", "args": args, "id": call_id, "type": "tool_call"}


def _explanation(
    dish: str = "Test Dish", replacements: list[Replacement] | None = None
) -> DishExplanation:
    return DishExplanation(dish=dish, explanation="because.", replacements=replacements or [])


class _Bound:
    def __init__(self, model: "_ScriptedChat") -> None:
        self._model = model

    async def ainvoke(self, _messages: object) -> AIMessage:
        turn = self._model.turns[self._model.calls]
        self._model.calls += 1
        if isinstance(turn, Exception):
            raise turn
        return turn


class _Structured:
    def __init__(self, explanation: DishExplanation) -> None:
        self._explanation = explanation

    async def ainvoke(self, _messages: object) -> DishExplanation:
        return self._explanation


class _ScriptedChat:
    """A stand-in chat model that replays scripted turns and a fixed explanation."""

    def __init__(self, turns: list[AIMessage | Exception], explanation: DishExplanation) -> None:
        self.turns = turns
        self.explanation = explanation
        self.calls = 0

    def bind_tools(self, _tools: object) -> _Bound:
        return _Bound(self)

    def with_structured_output(self, _schema: object) -> _Structured:
        return _Structured(self.explanation)


def _agent(chat: _ScriptedChat, service: IngredientService, **kwargs: int) -> DishLookupAgent:
    wrapper = ChatModel(model=chat, model_name="stub/model")  # type: ignore[arg-type]
    return DishLookupAgent(chat=wrapper, service=service, **kwargs)


async def test_verdict_comes_from_index_not_model_prose(session: AsyncSession) -> None:
    # The model's prose is cheery, but the index records tomato as incompatible.
    # The verdict is the index's, so it is AVOID regardless of what the model says.
    session.add(_ingredient("Tomato", compatibility=Compatibility.INCOMPATIBLE))
    await session.flush()
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=[_tool_call("tomato")]), AIMessage(content="done")],
        explanation=_explanation(dish="Tomato Soup"),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="tomato soup")

    assert result.verdict is SafetyLevel.AVOID
    assert result.dish == "Tomato Soup"
    assert result.model == "stub/model"
    assert chat.calls == 2  # tool turn, then the "done" turn


async def test_well_tolerated_ingredient_is_safe(session: AsyncSession) -> None:
    session.add(_ingredient("Lettuce", compatibility=Compatibility.WELL_TOLERATED))
    await session.flush()
    chat = _ScriptedChat(
        turns=[
            AIMessage(content="", tool_calls=[_tool_call("lettuce")]),
            AIMessage(content="done"),
        ],
        explanation=_explanation(dish="Garden Salad"),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="salad")

    assert result.verdict is SafetyLevel.SAFE


async def test_unrated_ingredient_carries_no_risk(session: AsyncSession) -> None:
    session.add(_ingredient("Bamboo Shoots"))  # no compatibility -> "unknown"
    await session.flush()
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=[_tool_call("bamboo shoots")]), AIMessage("done")],
        explanation=_explanation(),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="bamboo stir fry")

    assert result.verdict is SafetyLevel.SAFE


async def test_absent_ingredient_is_safe_despite_cautious_prose(session: AsyncSession) -> None:
    # "rice" is not in the index, which records only histamine-relevant foods, so
    # it carries no known risk. Cautious model prose cannot override that.
    session.add(_ingredient("Tomato", compatibility=Compatibility.INCOMPATIBLE))  # unrelated
    await session.flush()
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=[_tool_call("rice")]), AIMessage(content="done")],
        explanation=_explanation(dish="Boiled Rice"),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="boiled rice")

    assert result.verdict is SafetyLevel.SAFE


# --- the ambiguity rule (resolved at the SafetyLevel layer) ----------------------


async def test_safe_and_risky_readings_are_depends(session: AsyncSession) -> None:
    # egg yolk (well tolerated) vs egg white (incompatible): a real "depends which
    # form" case -> DEPENDS, not forced to AVOID.
    session.add(
        _ingredient("Egg Yolk", compatibility=Compatibility.WELL_TOLERATED, aliases=["egg"])
    )
    session.add(_ingredient("Egg White", compatibility=Compatibility.INCOMPATIBLE, aliases=["egg"]))
    await session.flush()
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=[_tool_call("egg")]), AIMessage(content="done")],
        explanation=_explanation(dish="Omelette"),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="omelette")

    assert result.verdict is SafetyLevel.DEPENDS


async def test_two_unsafe_readings_stay_avoid(session: AsyncSession) -> None:
    # Both readings are unsafe (incompatible + poorly tolerated). Disagreement at
    # the raw layer must NOT downgrade a unanimously-unsafe lookup to DEPENDS.
    session.add(_ingredient("Aged Salami", compatibility=Compatibility.INCOMPATIBLE, aliases=["x"]))
    session.add(
        _ingredient("Cured Ham", compatibility=Compatibility.POORLY_TOLERATED, aliases=["x"])
    )
    await session.flush()
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=[_tool_call("x")]), AIMessage(content="done")],
        explanation=_explanation(dish="Charcuterie"),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="charcuterie")

    assert result.verdict is SafetyLevel.AVOID


async def test_safe_and_unrated_readings_stay_safe(session: AsyncSession) -> None:
    # Well-tolerated + unrated both map to SAFE; the raw values differ, but that is
    # not a real disagreement, so the dish must not become DEPENDS.
    session.add(_ingredient("Rated Y", compatibility=Compatibility.WELL_TOLERATED, aliases=["y"]))
    session.add(_ingredient("Unrated Y", aliases=["y"]))  # no compatibility
    await session.flush()
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=[_tool_call("y")]), AIMessage(content="done")],
        explanation=_explanation(dish="Y Dish"),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="y dish")

    assert result.verdict is SafetyLevel.SAFE


# --- the grounding floor (incomplete or absent grounding -> DEPENDS) -------------


async def test_truncation_floors_a_safe_grounding_to_depends(session: AsyncSession) -> None:
    # The loop never finishes (always asks for another tool); grounding is
    # incomplete, so even an all-safe partial result must not assert SAFE.
    session.add(_ingredient("Lettuce", compatibility=Compatibility.WELL_TOLERATED))
    await session.flush()
    turns: list[AIMessage | Exception] = [
        AIMessage(content="", tool_calls=[_tool_call("lettuce", f"c{i}")]) for i in range(10)
    ]
    chat = _ScriptedChat(turns=turns, explanation=_explanation(dish="Salad"))

    result = await _agent(chat, IngredientService(session), max_iterations=3).run(dish="salad")

    assert chat.calls == 3  # stopped at the cap, not 10
    assert result.verdict is SafetyLevel.DEPENDS  # floored, not SAFE


async def test_iteration_cap_keeps_grounded_avoid(session: AsyncSession) -> None:
    session.add(_ingredient("Tomato", compatibility=Compatibility.INCOMPATIBLE))
    await session.flush()
    turns: list[AIMessage | Exception] = [
        AIMessage(content="", tool_calls=[_tool_call("tomato", f"c{i}")]) for i in range(10)
    ]
    chat = _ScriptedChat(turns=turns, explanation=_explanation(dish="Loop"))

    result = await _agent(chat, IngredientService(session), max_iterations=3).run(dish="loop")

    assert chat.calls == 3
    assert result.verdict is SafetyLevel.AVOID  # incomplete floor cannot lower AVOID


async def test_zero_lookups_floors_to_depends(session: AsyncSession) -> None:
    # The model makes no tool calls at all: nothing is grounded, so we cannot say
    # "safe" — the verdict is floored to DEPENDS.
    chat = _ScriptedChat(
        turns=[AIMessage(content="no dish here")], explanation=_explanation(dish="Unknown")
    )

    result = await _agent(chat, IngredientService(session)).run(dish="hello there")

    assert result.verdict is SafetyLevel.DEPENDS


# --- swap grounding --------------------------------------------------------------


async def test_safe_verdict_drops_any_replacements(session: AsyncSession) -> None:
    session.add(_ingredient("Lettuce", compatibility=Compatibility.WELL_TOLERATED))
    await session.flush()
    chat = _ScriptedChat(
        turns=[
            AIMessage(content="", tool_calls=[_tool_call("lettuce")]),
            AIMessage(content="done"),
        ],
        explanation=_explanation(
            dish="Salad",
            replacements=[Replacement(ingredient="lettuce", swap="kale", reason="why")],
        ),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="salad")

    assert result.verdict is SafetyLevel.SAFE
    assert result.replacements == []


async def test_unsafe_proposed_swap_is_dropped(session: AsyncSession) -> None:
    # The model suggests "tomato" as a swap, but the index flags tomato — it must
    # be validated out rather than shipped on an AVOID verdict.
    session.add(
        _ingredient("Parmesan", compatibility=Compatibility.INCOMPATIBLE, category="cheese")
    )
    session.add(_ingredient("Tomato", compatibility=Compatibility.INCOMPATIBLE))
    await session.flush()
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=[_tool_call("parmesan")]), AIMessage("done")],
        explanation=_explanation(
            dish="Pasta",
            replacements=[Replacement(ingredient="parmesan", swap="tomato", reason="no")],
        ),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="pasta")

    assert result.verdict is SafetyLevel.AVOID
    assert all(replacement.swap.lower() != "tomato" for replacement in result.replacements)


async def test_safe_proposed_swap_is_kept(session: AsyncSession) -> None:
    # "ricotta" is absent from the index (no recorded concern), so the swap stands.
    session.add(
        _ingredient("Parmesan", compatibility=Compatibility.INCOMPATIBLE, category="cheese")
    )
    await session.flush()
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=[_tool_call("parmesan")]), AIMessage("done")],
        explanation=_explanation(
            dish="Pasta",
            replacements=[Replacement(ingredient="parmesan", swap="ricotta", reason="fresh")],
        ),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="pasta")

    assert result.verdict is SafetyLevel.AVOID
    assert any(replacement.swap.lower() == "ricotta" for replacement in result.replacements)


async def test_missing_swap_is_filled_from_the_index(session: AsyncSession) -> None:
    # The model proposes no swap; a grounded same-category well-tolerated option
    # fills the gap so an AVOID dish is not left with an empty swap card.
    session.add(
        _ingredient("Parmesan", compatibility=Compatibility.INCOMPATIBLE, category="cheese")
    )
    session.add(
        _ingredient("Ricotta", compatibility=Compatibility.WELL_TOLERATED, category="cheese")
    )
    await session.flush()
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=[_tool_call("parmesan")]), AIMessage("done")],
        explanation=_explanation(dish="Pasta"),  # no replacements proposed
    )

    result = await _agent(chat, IngredientService(session)).run(dish="pasta")

    assert result.verdict is SafetyLevel.AVOID
    assert [replacement.swap for replacement in result.replacements] == ["Ricotta"]


# --- malformed tool calls and the tool-call budget ------------------------------


async def test_malformed_tool_call_does_not_crash(session: AsyncSession) -> None:
    # A tool call with no id and empty args (the tool schema needs `ingredient`):
    # the loop must recover, not 500. The failed lookup grounds nothing, so the
    # verdict floors to DEPENDS.
    bad_call = {"name": "lookup_ingredient_safety", "args": {}, "id": None, "type": "tool_call"}
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=[bad_call]), AIMessage(content="done")],
        explanation=_explanation(dish="Mystery"),
    )

    result = await _agent(chat, IngredientService(session)).run(dish="mystery")

    assert result.dish == "Mystery"  # completed without raising
    assert result.verdict is SafetyLevel.DEPENDS
    assert chat.calls == 2


async def test_tool_budget_bounds_fan_out(session: AsyncSession) -> None:
    # One turn asks for ten lookups; the budget stops dispatch mid-turn, so the
    # run never reaches the model again and the verdict floors (not SAFE).
    session.add(_ingredient("Lettuce", compatibility=Compatibility.WELL_TOLERATED))
    await session.flush()
    fan_out = [_tool_call("lettuce", f"c{i}") for i in range(10)]
    chat = _ScriptedChat(
        turns=[AIMessage(content="", tool_calls=fan_out), AIMessage(content="done")],
        explanation=_explanation(dish="Salad"),
    )

    result = await _agent(chat, IngredientService(session), max_tool_calls=3).run(dish="salad")

    assert chat.calls == 1  # the budget broke the run inside the first turn
    assert result.verdict is SafetyLevel.DEPENDS


async def test_model_failure_becomes_a_clean_domain_error(session: AsyncSession) -> None:
    chat = _ScriptedChat(turns=[RuntimeError("model down")], explanation=_explanation())

    with pytest.raises(LLMInvocationError):
        await _agent(chat, IngredientService(session)).run(dish="anything")
