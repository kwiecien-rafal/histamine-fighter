"""Tests for the agentic ComposerAgent (the model-driven tool-calling loop).

A scripted stand-in chat model replays tool-call turns while the lookups run
against the seeded test DB, so these exercise the code-owned safety gate (an
``avoid`` ingredient is rejected and fed back), the verdict gate requiring SAFE,
the iteration budget, the authored trace, and the per-iteration usage tally
without any network call.
"""

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composer import ComposerAgent, ComposerExhausted
from app.enums import Compatibility, MealType
from app.llm.langchain_factory import ChatModel
from app.models import HistamineIngredient
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService
from tests.fakes import FakeEmbedder

# Token usage every scripted reply reports, so the agent's tally is assertable.
_STEP_TOKENS = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

_SAFE_INGREDIENTS = [
    {"name": "courgette", "category": "vegetable"},
    {"name": "olive oil", "category": "oil"},
]
_UNSAFE_INGREDIENTS = [
    {"name": "parmesan", "category": "aged hard cheese"},
    {"name": "courgette", "category": "vegetable"},
]


def _ingredient(name: str, **kwargs: object) -> HistamineIngredient:
    return HistamineIngredient(name=name, sources=["test source"], **kwargs)


async def _seed(session: AsyncSession) -> None:
    session.add_all(
        [
            _ingredient(
                "parmesan", compatibility=Compatibility.INCOMPATIBLE, category="aged hard cheese"
            ),
            _ingredient(
                "courgette", compatibility=Compatibility.WELL_TOLERATED, category="vegetable"
            ),
        ]
    )
    await session.flush()


def _call(name: str, args: dict[str, Any], call_id: str = "call-1") -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _ai(*, content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> AIMessage:
    return AIMessage(content=content, tool_calls=tool_calls or [], usage_metadata=_STEP_TOKENS)


def _submit(
    name: str = "Courgette ribbons", ingredients: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return _call(
        "SubmitMeal",
        {
            "name": name,
            "description": "a light, fresh dish",
            "ingredients": _SAFE_INGREDIENTS if ingredients is None else ingredients,
            "recipe": ["Prep the ingredients.", "Plate and serve."],
            "tags": ["light", "raw"],
        },
    )


class _ScriptedToolChat:
    """A stand-in chat model that replays scripted tool-call turns in order."""

    def __init__(self, replies: list[AIMessage]) -> None:
        self._replies = list(replies)
        self.bound_tools: list[Any] | None = None
        self.invocations: list[Any] = []

    def bind_tools(self, tools: list[Any], **_kwargs: Any) -> "_ScriptedToolChat":
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages: Any) -> AIMessage:
        self.invocations.append(messages)
        if not self._replies:
            return _ai()
        return self._replies.pop(0)


def _agent(
    chat: _ScriptedToolChat,
    session: AsyncSession,
    fake_embedder: FakeEmbedder,
    *,
    max_iterations: int = 8,
) -> ComposerAgent:
    wrapper = ChatModel(model=chat, model_name="stub/model")  # type: ignore[arg-type]
    return ComposerAgent(
        chat=wrapper,
        ingredient_service=IngredientService(session),
        meal_service=MealService(session, fake_embedder),
        max_iterations=max_iterations,
    )


async def test_submit_with_safe_ingredients_is_accepted(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _seed(session)
    chat = _ScriptedToolChat([_ai(tool_calls=[_submit()])])

    meal = await _agent(chat, session, fake_embedder).compose(MealType.LUNCH)

    assert meal.name == "Courgette ribbons"
    assert meal.meal_type is MealType.LUNCH
    assert [item.name for item in meal.ingredients] == ["courgette", "olive oil"]
    assert meal.model == "stub/model"
    assert meal.recipe == ["Prep the ingredients.", "Plate and serve."]
    kinds = [event.kind for event in meal.reasoning_trace]
    assert kinds == ["submit", "verify"]
    # All four tools are bound, and the user turn carries the meal type in its region.
    assert chat.bound_tools is not None and len(chat.bound_tools) == 4
    assert "<brief>\nCompose one lunch meal.\n</brief>" in chat.invocations[0][1].content


async def test_submit_with_avoid_ingredient_is_rejected_then_revised(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _seed(session)
    chat = _ScriptedToolChat(
        [
            _ai(tool_calls=[_submit("Parmesan bowl", ingredients=_UNSAFE_INGREDIENTS)]),
            _ai(tool_calls=[_submit()]),
        ]
    )

    meal = await _agent(chat, session, fake_embedder).compose(MealType.DINNER)

    assert "parmesan" not in [item.name.casefold() for item in meal.ingredients]
    kinds = [event.kind for event in meal.reasoning_trace]
    assert kinds.count("submit") == 2
    assert kinds[-1] == "verify"
    reject = next(event for event in meal.reasoning_trace if event.kind == "reject")
    assert reject.ingredient == "parmesan"
    assert reject.compatibility == "avoid"


async def test_composer_exhausts_after_iteration_budget(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _seed(session)
    chat = _ScriptedToolChat(
        [
            _ai(tool_calls=[_submit("Parmesan bowl", ingredients=_UNSAFE_INGREDIENTS)]),
            _ai(tool_calls=[_submit("Parmesan plate", ingredients=_UNSAFE_INGREDIENTS)]),
        ]
    )

    with pytest.raises(ComposerExhausted):
        await _agent(chat, session, fake_embedder, max_iterations=2).compose(MealType.SNACK)


async def test_usage_is_tallied_per_iteration(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _seed(session)
    chat = _ScriptedToolChat(
        [
            _ai(tool_calls=[_call("LookupIngredientSafety", {"ingredient": "parmesan"})]),
            _ai(tool_calls=[_submit()]),
        ]
    )

    agent = _agent(chat, session, fake_embedder)
    await agent.compose(MealType.BREAKFAST)

    usage = agent._collect_usage()
    assert usage.calls == 2
    assert usage.total_tokens == 30
    assert [step.step for step in usage.steps] == ["compose", "compose"]


async def test_lookup_tool_records_a_check_event(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _seed(session)
    chat = _ScriptedToolChat(
        [
            _ai(tool_calls=[_call("LookupIngredientSafety", {"ingredient": "parmesan"})]),
            _ai(tool_calls=[_submit()]),
        ]
    )

    meal = await _agent(chat, session, fake_embedder).compose(MealType.BREAKFAST)

    check = next(event for event in meal.reasoning_trace if event.kind == "check")
    assert check.ingredient == "parmesan"
    assert check.compatibility == "avoid"


async def test_reply_without_tool_calls_is_nudged_and_drafts_are_captured(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _seed(session)
    chat = _ScriptedToolChat(
        [
            _ai(content="Let me sketch a fresh breakfast."),
            _ai(tool_calls=[_submit()]),
        ]
    )

    meal = await _agent(chat, session, fake_embedder).compose(MealType.BREAKFAST)

    assert meal.name == "Courgette ribbons"
    assert any(event.kind == "draft" for event in meal.reasoning_trace)


async def test_stream_yields_events_then_the_meal(
    session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _seed(session)
    chat = _ScriptedToolChat(
        [
            _ai(tool_calls=[_call("LookupIngredientSafety", {"ingredient": "courgette"})]),
            _ai(tool_calls=[_submit()]),
        ]
    )

    chunks = [chunk async for chunk in _agent(chat, session, fake_embedder).stream(MealType.LUNCH)]

    assert any('"kind"' in chunk for chunk in chunks)
    assert '"meal_type"' in chunks[-1]
