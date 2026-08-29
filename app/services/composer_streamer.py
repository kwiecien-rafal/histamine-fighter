"""Live composer stream for the admin compose triggers.

The composer is expensive and normally runs offline (the cron writes the board);
this drives a single live run as Server-Sent Events, so an admin watches the agent
compose one meal in real time. It owns a database session for the life of the stream
rather than the request-scoped one, because a streaming response outlives the request
that started it.

The curated and daily triggers pass a ``persist`` callback that writes the finished,
trace-carrying meal on the stream's own session; the streamer commits it and emits a
final ``saved`` frame, or an ``error`` frame if the write fails (tokens already spent,
nothing stored, retryable).
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composer import ComposerAgent
from app.agents.inspiration import InspirationBrief, sample_brief
from app.agents.meal_judge import MealJudgeAgent
from app.config import settings
from app.db.engine import SessionLocal
from app.embeddings import Embedder
from app.enums import MealType
from app.llm.langchain_factory import ChatModel
from app.schemas.meal import ComposedMeal, ComposedMealCard, SavedEvent
from app.services.daily_service import DailyService
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
        self, meal_type: MealType, *, persist: Persist, inspiration_date: date | None = None
    ) -> AsyncIterator[dict[str, str]]:
        """Yield SSE frames: a ``trace`` per step, the ``meal``, then ``saved``/``error``.

        The finished meal is written and committed on the stream's own session after the
        ``meal`` frame, then confirmed with a ``saved`` frame carrying its id (or an
        ``error`` frame if the write fails). A fresh inspiration brief is drawn per run,
        so re-generating a slot gives a different direction; ``inspiration_date`` (the
        daily routes) adds the boards near that date to the do-not-repeat list.
        """
        async with SessionLocal() as session:
            ingredient_service = IngredientService(session)
            agent = ComposerAgent(
                chat=self._chat,
                ingredient_service=ingredient_service,
                meal_service=MealService(session, self._embedder),
                judge=MealJudgeAgent(self._chat) if settings.composer_judge_enabled else None,
            )
            brief = await self._draw_brief(session, ingredient_service, meal_type, inspiration_date)
            async for item in agent.events(meal_type, inspiration=brief):
                if isinstance(item, ComposedMeal):
                    yield _frame("meal", ComposedMealCard.from_meal(item).model_dump_json())
                    yield await self._save(item, session, persist)
                else:
                    yield _frame("trace", item.model_dump_json())

    @staticmethod
    async def _draw_brief(
        session: AsyncSession,
        ingredient_service: IngredientService,
        meal_type: MealType,
        inspiration_date: date | None,
    ) -> InspirationBrief:
        avoid: list[str] = []
        if inspiration_date is not None:
            avoid = await DailyService(session).recent_meal_names(
                before=inspiration_date, days=settings.daily_variety_window_days
            )
        return sample_brief(
            meal_type,
            hero_pool=await ingredient_service.well_tolerated_pool(),
            avoid_names=avoid,
        )

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
