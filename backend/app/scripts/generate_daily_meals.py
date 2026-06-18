"""Compose a day's board offline and store each meal as pending admin review.

Runs the agentic ComposerAgent once per meal type for a target date and persists
each result as a daily_suggestions row (approval_status=pending) with its recorded
trace and a reveal time of ``settings.daily_reveal_hour_utc`` (UTC) on that date. An
admin approves during the day; the public board unlocks at the reveal time, replaying
the trace as the premiere. Cron-invoked the night before; the expensive composition
runs offline so the board read stays a cheap clock check.

Each composed meal is committed on its own, so a failure partway through keeps the
meals already done and a re-run only fills what is missing. A slot already holding a
pending or approved suggestion is left untouched; a previously rejected slot is
recomposed in place, which is the admin's path to a replacement after a rejection. A
run that exhausts its budget or hits a model error logs and skips that slot.

The composer needs a tool-calling model. The default ``llm_provider`` is ollama, so
either run a tools-capable local model or point the composer at a capable provider.

Run it (database up, migrations applied, a tool-calling model configured):

    uv run --directory backend python -m app.scripts.generate_daily_meals
    uv run --directory backend python -m app.scripts.generate_daily_meals --date 2026-06-20
    uv run --directory backend python -m app.scripts.generate_daily_meals --meal-type dinner
"""

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composer import ComposerAgent, ComposerExhausted
from app.config import settings
from app.core.logging import configure_logging
from app.db.engine import SessionLocal
from app.embeddings import Embedder, get_embedder
from app.enums import ApprovalStatus, MealType
from app.llm.config import LLMRequestConfig
from app.llm.errors import LLMError
from app.llm.langchain_factory import build_chat_model
from app.models import DailySuggestion
from app.schemas.daily import DailyMealContent
from app.schemas.meal import ComposedMeal
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService

log = structlog.get_logger()

# Composes one meal for a slot, raising ComposerExhausted or LLMError on failure.
ComposeFn = Callable[[MealType], Awaitable[ComposedMeal]]
# The durability boundary: commit per meal in production, flush under test isolation.
Checkpoint = Callable[[], Awaitable[None]]


def _build_agent(session: AsyncSession, embedder: Embedder) -> ComposerAgent:
    # No request in scope, so the provider resolves from settings, not X-LLM headers.
    chat = build_chat_model(LLMRequestConfig(), temperature=settings.compose_temperature)
    return ComposerAgent(
        chat=chat,
        ingredient_service=IngredientService(session),
        meal_service=MealService(session, embedder),
    )


def _reveal_at(target: date) -> datetime:
    """The instant the target date's board unlocks: a fixed UTC hour, same for all."""
    return datetime.combine(target, time(hour=settings.daily_reveal_hour_utc), tzinfo=UTC)


async def _row_for(
    session: AsyncSession, target: date, meal_type: MealType
) -> DailySuggestion | None:
    stmt = select(DailySuggestion).where(
        DailySuggestion.suggestion_date == target,
        DailySuggestion.meal_type == meal_type,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _fill(row: DailySuggestion, meal: ComposedMeal, target: date, reveal_at: datetime) -> None:
    """Write a composed meal onto a row, (re)setting it to pending review."""
    content = DailyMealContent(
        name=meal.name,
        description=meal.description,
        ingredients=meal.ingredients,
        recipe=meal.recipe,
        tags=meal.tags,
        unverified_ingredients=meal.unverified_ingredients,
    )
    row.suggestion_date = target
    row.meal_type = meal.meal_type
    row.content = content.model_dump()
    row.model = meal.model
    row.usage = meal.usage.model_dump()
    row.reasoning_trace = [event.model_dump() for event in meal.reasoning_trace]
    row.reveal_at = reveal_at
    row.approval_status = ApprovalStatus.PENDING
    row.approved_at = None
    row.approved_by = None


async def _compose_one(compose: ComposeFn, meal_type: MealType) -> ComposedMeal | None:
    """Compose one meal, or None when the composer cannot finish a safe one."""
    try:
        return await compose(meal_type)
    except ComposerExhausted:
        log.warning("daily.exhausted", meal_type=meal_type.value)
        return None
    except LLMError as exc:
        log.warning("daily.failed", meal_type=meal_type.value, error=str(exc))
        return None


async def build_board(
    session: AsyncSession,
    compose: ComposeFn,
    target: date,
    *,
    meal_types: Iterable[MealType],
    checkpoint: Checkpoint | None = None,
) -> int:
    """Compose the missing meals for a date, persisting each one as it is finished.

    Skips a slot that already holds a pending or approved suggestion; recomposes a
    rejected slot in place. Each meal is checkpointed on its own so a later failure
    cannot discard earlier work. Returns how many meals were stored.
    """
    persist = checkpoint or session.commit
    reveal_at = _reveal_at(target)
    stored = 0
    for meal_type in meal_types:
        existing = await _row_for(session, target, meal_type)
        if existing is not None and existing.approval_status is not ApprovalStatus.REJECTED:
            log.info("daily.skip_existing", date=target.isoformat(), meal_type=meal_type.value)
            continue
        meal = await _compose_one(compose, meal_type)
        if meal is None:
            continue
        row = existing or DailySuggestion()
        _fill(row, meal, target, reveal_at)
        if existing is None:
            session.add(row)
        await persist()
        stored += 1
        log.info(
            "daily.stored",
            date=target.isoformat(),
            meal_type=meal_type.value,
            recomposed=existing is not None,
        )
    return stored


async def generate(target: date, meal_types: Sequence[MealType]) -> None:
    """Compose the target date's board and store the successes as pending rows."""
    embedder = get_embedder()
    async with SessionLocal() as session:
        agent = _build_agent(session, embedder)
        stored = await build_board(session, agent.compose, target, meal_types=meal_types)
    log.info("daily.done", date=target.isoformat(), stored=stored, requested=len(meal_types))


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compose a day's daily board.")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Target date as YYYY-MM-DD. Defaults to tomorrow (UTC).",
    )
    parser.add_argument(
        "--meal-type",
        choices=[meal_type.value for meal_type in MealType],
        default=None,
        help="Compose only this slot. Defaults to all four.",
    )
    return parser.parse_args(argv)


def main() -> None:
    configure_logging()
    args = _parse_args()
    target = args.date or (datetime.now(UTC).date() + timedelta(days=1))
    meal_types = [MealType(args.meal_type)] if args.meal_type else list(MealType)
    asyncio.run(generate(target, meal_types))


if __name__ == "__main__":
    main()
