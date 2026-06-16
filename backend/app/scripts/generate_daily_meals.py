"""Compose tomorrow's daily board offline and store it as pending admin review.

Runs the agentic ComposerAgent once per meal type for the next day and persists each
result as a daily_suggestions row (approval_status=pending) with its recorded trace
and a reveal time of 10:00 on that day. An admin approves during the day; the public
board unlocks at the reveal time, replaying the trace as the premiere. Cron-invoked
the night before; the expensive composition runs offline so the board read stays a
cheap clock check.

Idempotent: a meal type already scheduled for the target date is left untouched, so a
re-run only fills gaps and never clobbers an approved suggestion. A run that exhausts
its budget or hits a model error is logged and skipped, like the curated-pool batch.

The composer needs a tool-calling model. The default ``llm_provider`` is ollama, so
either run a tools-capable local model or point the composer at a capable provider.

Run it (database up, migrations applied, a tool-calling model configured):

    uv run --directory backend python -m app.scripts.generate_daily_meals
"""

import asyncio
from datetime import UTC, date, datetime, time, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composer import ComposerAgent, ComposerExhausted
from app.core.logging import configure_logging
from app.db.engine import SessionLocal
from app.embeddings import Embedder, get_embedder
from app.enums import ApprovalStatus, MealType
from app.llm.config import LLMRequestConfig
from app.llm.errors import LLMError
from app.llm.langchain_factory import build_chat_model
from app.models import DailySuggestion
from app.schemas.daily import DailyMealContent
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService

log = structlog.get_logger()

# Slightly creative so the board varies day to day; the index still gates safety.
_COMPOSE_TEMPERATURE = 0.4
# The board unlocks at this hour (UTC) on its date, the same instant for everyone.
_REVEAL_HOUR = 10


def _build_agent(session: AsyncSession, embedder: Embedder) -> ComposerAgent:
    # No request in scope, so the provider resolves from settings, not X-LLM headers.
    chat = build_chat_model(LLMRequestConfig(), temperature=_COMPOSE_TEMPERATURE)
    return ComposerAgent(
        chat=chat,
        ingredient_service=IngredientService(session),
        meal_service=MealService(session, embedder),
    )


async def _already_scheduled(session: AsyncSession, target: date) -> set[MealType]:
    """The meal types that already have a suggestion for the target date."""
    rows = await session.execute(
        select(DailySuggestion.meal_type).where(DailySuggestion.suggestion_date == target)
    )
    return set(rows.scalars().all())


async def _compose_one(
    agent: ComposerAgent, meal_type: MealType, target: date, reveal_at: datetime
) -> DailySuggestion | None:
    """Compose one meal and shape it into a pending board row, or None when it fails."""
    try:
        meal = await agent.compose(meal_type)
    except ComposerExhausted:
        log.warning("daily.exhausted", meal_type=meal_type.value)
        return None
    except LLMError as exc:
        log.warning("daily.failed", meal_type=meal_type.value, error=str(exc))
        return None

    content = DailyMealContent(
        name=meal.name,
        description=meal.description,
        ingredients=meal.ingredients,
        recipe=meal.recipe,
        tags=meal.tags,
    )
    return DailySuggestion(
        suggestion_date=target,
        meal_type=meal.meal_type,
        content=content.model_dump(),
        model=meal.model,
        reasoning_trace=[event.model_dump() for event in meal.reasoning_trace],
        reveal_at=reveal_at,
        approval_status=ApprovalStatus.PENDING,
    )


async def generate(target: date) -> None:
    """Compose the missing meals for the target date and store them as pending rows."""
    reveal_at = datetime.combine(target, time(hour=_REVEAL_HOUR), tzinfo=UTC)
    embedder = get_embedder()
    stored = 0
    async with SessionLocal() as session:
        scheduled = await _already_scheduled(session, target)
        agent = _build_agent(session, embedder)
        for meal_type in MealType:
            if meal_type in scheduled:
                log.info("daily.skip_existing", date=target.isoformat(), meal_type=meal_type.value)
                continue
            row = await _compose_one(agent, meal_type, target, reveal_at)
            if row is not None:
                session.add(row)
                stored += 1
                log.info("daily.stored", date=target.isoformat(), meal_type=meal_type.value)
        await session.commit()
    log.info("daily.done", date=target.isoformat(), stored=stored, requested=len(MealType))


def main() -> None:
    configure_logging()
    target = datetime.now(UTC).date() + timedelta(days=1)
    asyncio.run(generate(target))


if __name__ == "__main__":
    main()
