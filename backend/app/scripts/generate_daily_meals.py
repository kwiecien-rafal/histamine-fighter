"""Compose a day's board offline and store each meal as pending admin review.

Runs the agentic ComposerAgent once per meal type for a target date and persists
each result as a daily_suggestions row (approval_status=pending) with its recorded
trace and a reveal time of ``settings.daily_reveal_hour_utc`` (UTC) on that date. An
admin approves during the day; the public board unlocks at the reveal time, where each
meal offers an on-demand replay of how it was composed. Cron runs nightly as a
gap-filler: it covers the next ``daily_cron_horizon_days`` days starting tomorrow, so a
partial day is completed and an empty day between two filled days is backfilled, then
prunes boards older than the history window so the table stays bounded to what the
public past-board view can read. The expensive composition runs offline so the board
read stays a cheap clock check.

Each composed meal is committed on its own, so a failure partway through keeps the
meals already done and a re-run only fills what is missing. A slot already holding a
pending or approved suggestion is left untouched; a previously rejected slot is
recomposed in place, which is the admin's path to a replacement after a rejection. A
run that exhausts its budget or hits a model error logs and skips that slot.

The composer needs a tool-calling model. The default ``llm_provider`` is ollama, so
either run a tools-capable local model or point the composer at a capable provider.

Run it (database up, migrations applied, a tool-calling model configured):

    uv run --directory backend python -m app.scripts.generate_daily_meals
    uv run --directory backend python -m app.scripts.generate_daily_meals --horizon 7
    uv run --directory backend python -m app.scripts.generate_daily_meals --date 2026-06-20
    uv run --directory backend python -m app.scripts.generate_daily_meals --meal-type dinner
"""

import argparse
import asyncio
import random
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composer import ComposerAgent, ComposerExhausted
from app.agents.inspiration import sample_brief
from app.agents.meal_judge import MealJudgeAgent
from app.config import settings
from app.core.logging import configure_logging
from app.db.engine import SessionLocal
from app.embeddings import Embedder, get_embedder
from app.enums import ApprovalStatus, MealType
from app.llm.config import LLMRequestConfig
from app.llm.errors import LLMError
from app.llm.langchain_factory import build_chat_model
from app.models import GenerationSettings
from app.schemas.meal import ComposedMeal
from app.services.daily_service import DailyService
from app.services.generation_settings_service import GenerationSettingsService
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService

log = structlog.get_logger()

# Composes one meal for a dated slot, raising ComposerExhausted or LLMError on failure.
ComposeFn = Callable[[MealType, date], Awaitable[ComposedMeal]]
# The durability boundary: commit per meal in production, flush under test isolation.
Checkpoint = Callable[[], Awaitable[None]]

# One resample after an exhausted run: a fresh direction often converges where the
# first draw could not, and a second failure means the slot is skipped anyway.
_SLOT_ATTEMPTS = 2


def _build_agent(
    gen_settings: GenerationSettings, session: AsyncSession, embedder: Embedder
) -> ComposerAgent:
    # No request in scope, so the provider comes from the operator-set settings.
    chat = build_chat_model(
        LLMRequestConfig(
            provider=gen_settings.composer_provider, model=gen_settings.composer_model
        ),
        temperature=settings.compose_temperature,
    )
    return ComposerAgent(
        chat=chat,
        ingredient_service=IngredientService(session),
        meal_service=MealService(session, embedder),
        judge=MealJudgeAgent(chat) if settings.composer_judge_enabled else None,
    )


def _slot_rng(target: date, meal_type: MealType, attempt: int) -> random.Random:
    """A reproducible entropy source per (date, slot, attempt).

    Re-running the same day redraws the same brief, so a failed cron run can be
    replayed and debugged; the attempt varies the draw on a resample.
    """
    return random.Random(f"{target.isoformat()}:{meal_type.value}:{attempt}")


async def _compose_one(
    compose: ComposeFn, meal_type: MealType, target: date
) -> ComposedMeal | None:
    """Compose one meal, or None when the composer cannot finish a safe one."""
    try:
        return await compose(meal_type, target)
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
    now: datetime | None = None,
    checkpoint: Checkpoint | None = None,
) -> int:
    """Compose the missing meals for a date, persisting each one as it is finished.

    Skips a slot that already holds a pending or approved suggestion; recomposes a
    rejected slot in place. Each meal is checkpointed on its own so a later failure
    cannot discard earlier work. Returns how many meals were stored.
    """
    persist = checkpoint or session.commit
    daily = DailyService(session)
    moment = now or datetime.now(UTC)
    stored = 0
    for meal_type in meal_types:
        existing = await daily.slot_for(target, meal_type)
        if existing is not None and existing.approval_status is not ApprovalStatus.REJECTED:
            log.info("daily.skip_existing", date=target.isoformat(), meal_type=meal_type.value)
            continue
        meal = await _compose_one(compose, meal_type, target)
        if meal is None:
            continue
        await daily.store_pending(meal, target, now=moment)
        await persist()
        stored += 1
        log.info(
            "daily.stored",
            date=target.isoformat(),
            meal_type=meal_type.value,
            recomposed=existing is not None,
        )
    return stored


async def build_boards(
    session: AsyncSession,
    compose: ComposeFn,
    targets: Sequence[date],
    *,
    meal_types: Sequence[MealType],
    now: datetime | None = None,
    checkpoint: Checkpoint | None = None,
) -> int:
    """Fill each target date's board, reusing build_board's per-slot predicate.

    The nightly gap-filler over a look-ahead horizon: a covered slot is skipped, a
    partial day completed, a rejected slot recomposed, and an empty day between two
    covered days backfilled. Idempotent, so a re-run stores only what is missing. One
    clock is shared across the dates so the same-day reveal clamp stays consistent.
    """
    moment = now or datetime.now(UTC)
    stored = 0
    for target in targets:
        stored += await build_board(
            session, compose, target, meal_types=meal_types, now=moment, checkpoint=checkpoint
        )
    return stored


def _inspired_compose(agent: ComposerAgent, session: AsyncSession) -> ComposeFn:
    """Wrap the agent with a per-slot drawn brief, resampling once on exhaustion.

    The brief carries the variety: a hero drawn from the index's well-tolerated
    rows, a code-sampled direction, and the recent boards as a do-not-repeat list.
    An exhausted run gets one fresh draw before the slot is given up, since a new
    direction often converges where the first could not.
    """
    daily = DailyService(session)
    ingredients = IngredientService(session)

    async def compose(meal_type: MealType, target: date) -> ComposedMeal:
        hero_pool = await ingredients.well_tolerated_pool()
        avoid = await daily.recent_meal_names(
            before=target, days=settings.daily_variety_window_days
        )
        for attempt in range(_SLOT_ATTEMPTS - 1):
            brief = sample_brief(
                meal_type,
                hero_pool=hero_pool,
                avoid_names=avoid,
                rng=_slot_rng(target, meal_type, attempt),
            )
            try:
                return await agent.compose(meal_type, inspiration=brief)
            except ComposerExhausted:
                log.warning("daily.resample", meal_type=meal_type.value, date=target.isoformat())
        brief = sample_brief(
            meal_type,
            hero_pool=hero_pool,
            avoid_names=avoid,
            rng=_slot_rng(target, meal_type, _SLOT_ATTEMPTS - 1),
        )
        return await agent.compose(meal_type, inspiration=brief)

    return compose


async def generate(targets: Sequence[date], meal_types: Sequence[MealType]) -> None:
    """Compose each target date's board and store the successes as pending rows."""
    embedder = get_embedder()
    async with SessionLocal() as session:
        gen_settings = await GenerationSettingsService(session).get()
        try:
            agent = _build_agent(gen_settings, session, embedder)
        except LLMError as exc:
            # The operator-set provider can drift out from under cron (a rotated key,
            # or public_deployment flipped with ollama saved). Fail fast and loud.
            log.error(
                "compose.settings.invalid",
                provider=gen_settings.composer_provider,
                model=gen_settings.composer_model,
                error=str(exc),
            )
            raise SystemExit(1) from exc
        stored = await build_boards(
            session, _inspired_compose(agent, session), targets, meal_types=meal_types
        )
        cutoff = datetime.now(UTC).date() - timedelta(days=settings.daily_history_days)
        pruned = await DailyService(session).prune_before(cutoff)
        await session.commit()
    log.info(
        "daily.done",
        dates=[target.isoformat() for target in targets],
        stored=stored,
        pruned=pruned,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compose the daily board over a horizon.")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Compose only this date (YYYY-MM-DD), overriding the horizon.",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=None,
        help="Days from tomorrow to cover. Defaults to settings.daily_cron_horizon_days.",
    )
    parser.add_argument(
        "--meal-type",
        choices=[meal_type.value for meal_type in MealType],
        default=None,
        help="Compose only this slot. Defaults to all four.",
    )
    return parser.parse_args(argv)


def _target_dates(args: argparse.Namespace) -> list[date]:
    """Resolve the dates to compose: a forced single date, else the cron horizon."""
    if args.date is not None:
        return [args.date]
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    horizon = args.horizon if args.horizon is not None else settings.daily_cron_horizon_days
    return [tomorrow + timedelta(days=offset) for offset in range(horizon)]


def main() -> None:
    configure_logging()
    args = _parse_args()
    meal_types = [MealType(args.meal_type)] if args.meal_type else list(MealType)
    asyncio.run(generate(_target_dates(args), meal_types))


if __name__ == "__main__":
    main()
