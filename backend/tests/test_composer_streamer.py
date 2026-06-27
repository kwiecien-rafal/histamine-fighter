"""Unit tests for the live composer streamer's orchestration.

These drive the real :class:`ComposerStreamer` with a scripted agent (no model) and a
fake session (no database), so they cover the glue the endpoint tests skip when they
swap the whole streamer out: consuming ``events()``, building the meal frame from the
rich meal, and the save path's commit, ``saved`` frame, and rollback-to-``error`` on a
persist failure. The actual row write lives in the persist callback, tested elsewhere.
"""

import json
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import Embedder
from app.enums import MealType
from app.llm.langchain_factory import ChatModel
from app.schemas.meal import ComposedMeal, ProposedIngredient, TraceEvent
from app.services import composer_streamer
from app.services.composer_streamer import ComposerStreamer
from tests.fakes import FakeEmbedder


def _composed_meal(meal_type: MealType) -> ComposedMeal:
    return ComposedMeal(
        name="Courgette ribbon salad",
        meal_type=meal_type,
        description="raw courgette ribbons with olive oil and fresh herbs",
        ingredients=[ProposedIngredient(name="courgette", category="vegetable")],
        recipe=["Peel into ribbons."],
        tags=["fresh"],
        unverified_ingredients=[],
        model="fake/test",
        reasoning_trace=[TraceEvent(kind="verify", text="Courgette cleared the index.")],
    )


class _FakeAgent:
    """Stand-in for ComposerAgent: events() replays a scripted run, no model, no tools."""

    def __init__(self, **_: object) -> None:
        pass

    async def events(self, meal_type: MealType) -> AsyncIterator[TraceEvent | ComposedMeal]:
        yield TraceEvent(kind="check", text="Checked courgette: well tolerated.")
        yield TraceEvent(kind="verify", text="Courgette cleared the index.")
        yield _composed_meal(meal_type)


class _FakeSession:
    """Records commit/rollback so the save path is assertable without a database."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture
def _streamer(monkeypatch: pytest.MonkeyPatch) -> tuple[ComposerStreamer, _FakeSession]:
    """The real streamer wired to a scripted agent and a fake, commit-recording session."""
    session = _FakeSession()
    monkeypatch.setattr(composer_streamer, "ComposerAgent", _FakeAgent)
    monkeypatch.setattr(composer_streamer, "SessionLocal", lambda: session)
    streamer = ComposerStreamer(
        chat=cast(ChatModel, object()), embedder=cast(Embedder, FakeEmbedder())
    )
    return streamer, session


async def test_preview_streams_trace_then_meal_without_saving(
    _streamer: tuple[ComposerStreamer, _FakeSession],
) -> None:
    streamer, session = _streamer

    frames = [frame async for frame in streamer.stream(MealType.LUNCH)]

    assert [frame["event"] for frame in frames] == ["trace", "trace", "meal"]
    meal_frame = frames[-1]
    assert "Courgette ribbon salad" in meal_frame["data"]
    assert "reasoning_trace" not in meal_frame["data"]  # the card drops the trace
    assert not session.committed


async def test_save_commits_and_emits_a_saved_frame(
    _streamer: tuple[ComposerStreamer, _FakeSession],
) -> None:
    streamer, session = _streamer
    saved_id = uuid4()

    async def persist(_meal: ComposedMeal, _db: AsyncSession) -> UUID:
        return saved_id

    frames = [frame async for frame in streamer.stream(MealType.LUNCH, persist=persist)]

    assert [frame["event"] for frame in frames] == ["trace", "trace", "meal", "saved"]
    assert json.loads(frames[-1]["data"]) == {"id": str(saved_id)}
    assert session.committed
    assert not session.rolled_back


async def test_a_persist_failure_rolls_back_and_emits_an_error_frame(
    _streamer: tuple[ComposerStreamer, _FakeSession],
) -> None:
    streamer, session = _streamer

    async def persist(_meal: ComposedMeal, _db: AsyncSession) -> UUID:
        raise RuntimeError("write failed")

    frames = [frame async for frame in streamer.stream(MealType.LUNCH, persist=persist)]

    assert [frame["event"] for frame in frames] == ["trace", "trace", "meal", "error"]
    assert "could not be saved" in json.loads(frames[-1]["data"])["detail"]
    assert session.rolled_back
    assert not session.committed
