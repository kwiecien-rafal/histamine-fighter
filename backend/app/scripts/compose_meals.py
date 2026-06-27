"""Compose histamine-safe meals offline and store them as pending admin review.

Runs the agentic ComposerAgent once per meal type and persists each result to
curated_meals as approval_status=pending, with its reasoning trace and an embedding
for later retrieval. The cron job and the admin trigger reuse the same agent; this
is the headless entry point.

The composer needs a tool-calling model. The default ``llm_provider`` is ollama, so
either run a tools-capable local model or point the composer at a capable provider.
A model that cannot call tools, or a run that exhausts its budget, is logged and
skipped rather than aborting the batch.

Run it (database up, migrations applied, a tool-calling model configured):

    uv run --directory backend python -m app.scripts.compose_meals
"""

import asyncio

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composer import ComposerAgent, ComposerExhausted
from app.config import settings
from app.core.logging import configure_logging
from app.db.engine import SessionLocal
from app.embeddings import Embedder, get_embedder
from app.enums import MealType
from app.llm.config import LLMRequestConfig
from app.llm.errors import LLMError
from app.llm.langchain_factory import build_chat_model
from app.models import GenerationSettings
from app.schemas.meal import ComposedMeal
from app.services.generation_settings_service import GenerationSettingsService
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService

log = structlog.get_logger()


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
    )


async def _compose_one(agent: ComposerAgent, meal_type: MealType) -> ComposedMeal | None:
    """Compose one meal, or None when the composer cannot finish a safe one."""
    try:
        return await agent.compose(meal_type)
    except ComposerExhausted:
        log.warning("compose.exhausted", meal_type=meal_type.value)
        return None
    except LLMError as exc:
        log.warning("compose.failed", meal_type=meal_type.value, error=str(exc))
        return None


async def compose_all() -> None:
    """Compose one meal per meal type and store the successes as pending rows."""
    embedder = get_embedder()
    stored = 0
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
        meal_service = MealService(session, embedder)
        for meal_type in MealType:
            meal = await _compose_one(agent, meal_type)
            if meal is not None:
                row = await meal_service.store_pending(meal)
                stored += 1
                log.info("compose.stored", meal_type=meal_type.value, name=row.name)
        await session.commit()
    log.info("compose.done", stored=stored, requested=len(MealType))


def main() -> None:
    configure_logging()
    asyncio.run(compose_all())


if __name__ == "__main__":
    main()
