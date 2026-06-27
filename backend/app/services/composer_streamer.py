"""Live composer stream for the admin compose triggers.

The composer is expensive and normally runs offline (the cron writes the board);
this drives a single live run as Server-Sent Events, so an admin watches the agent
compose one meal in real time. It owns a database session for the life of the stream
rather than the request-scoped one, because a streaming response outlives the request
that started it.

The preview trigger discards the result (``persist=None``), exactly the old demo. The
curated and daily save triggers pass a ``persist`` callback that writes the finished,
trace-carrying meal on the stream's own session; the streamer commits it and emits a
final ``saved`` frame, or an ``error`` frame if the write fails (tokens already spent,
nothing stored, retryable).
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composer import ComposerAgent
from app.db.engine import SessionLocal
from app.embeddings import Embedder
from app.enums import MealType
from app.llm.langchain_factory import ChatModel
from app.schemas.meal import ComposedMeal, ComposedMealCard, SavedEvent
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService

log = structlog.get_logger(__name__)

# Persists the finished meal on the stream's session and returns its new row id. The
# streamer owns the commit, so the callback only adds and flushes.
Persist = Callable[[ComposedMeal, AsyncSession], Awaitable[UUID]]

_SAVE_FAILED = "The meal was composed but could not be saved. Try again."


class ComposerStreamer:
    """Builds a composer over a stream-scoped session and yields its trace as SSE."""

    def __init__(self, chat: ChatModel, embedder: Embedder) -> None:
        self._chat = chat
        self._embedder = embedder

    async def stream(
        self, meal_type: MealType, *, persist: Persist | None = None
    ) -> AsyncIterator[dict[str, str]]:
        """Yield SSE frames: a ``trace`` per step, the ``meal``, then ``saved``/``error``.

        When ``persist`` is set, the finished meal is written and committed on the
        stream's own session after the ``meal`` frame, then confirmed with a ``saved``
        frame carrying its id. ``persist=None`` is the non-saving preview.
        """
        async with SessionLocal() as session:
            agent = ComposerAgent(
                chat=self._chat,
                ingredient_service=IngredientService(session),
                meal_service=MealService(session, self._embedder),
            )
            async for item in agent.events(meal_type):
                if isinstance(item, ComposedMeal):
                    yield _frame("meal", ComposedMealCard.from_meal(item).model_dump_json())
                    if persist is not None:
                        yield await self._save(item, session, persist)
                else:
                    yield _frame("trace", item.model_dump_json())

    async def _save(
        self, meal: ComposedMeal, session: AsyncSession, persist: Persist
    ) -> dict[str, str]:
        """Persist the composed meal, returning a ``saved`` frame or an ``error`` one."""
        try:
            saved_id = await persist(meal, session)
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("composer.save.failed", meal_type=meal.meal_type.value)
            return _frame("error", json.dumps({"detail": _SAVE_FAILED}))
        log.info("composer.save.done", meal_type=meal.meal_type.value, id=str(saved_id))
        return _frame("saved", SavedEvent(id=saved_id).model_dump_json())


def _frame(event: str, data: str) -> dict[str, str]:
    return {"event": event, "data": data}
