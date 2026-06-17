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
from app.core.logging import configure_logging
from app.db.engine import SessionLocal
from app.embeddings import Embedder, get_embedder
from app.enums import ApprovalStatus, MealType
from app.llm.config import LLMRequestConfig
from app.llm.errors import LLMError
from app.llm.langchain_factory import build_chat_model
from app.models import CuratedMeal
from app.models.curated_meal import meal_embedding_text
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService

log = structlog.get_logger()

# Slightly creative so the meals vary day to day; the index still gates safety.
_COMPOSE_TEMPERATURE = 0.4


def _build_agent(session: AsyncSession, embedder: Embedder) -> ComposerAgent:
    # No request in scope, so the provider resolves from settings, not X-LLM headers.
    chat = build_chat_model(LLMRequestConfig(), temperature=_COMPOSE_TEMPERATURE)
    return ComposerAgent(
        chat=chat,
        ingredient_service=IngredientService(session),
        meal_service=MealService(session, embedder),
    )


async def _compose_one(
    agent: ComposerAgent, embedder: Embedder, meal_type: MealType
) -> CuratedMeal | None:
    """Compose one meal and shape it into a pending row, or None when it fails."""
    try:
        meal = await agent.compose(meal_type)
    except ComposerExhausted:
        log.warning("compose.exhausted", meal_type=meal_type.value)
        return None
    except LLMError as exc:
        log.warning("compose.failed", meal_type=meal_type.value, error=str(exc))
        return None

    vector = (
        await embedder.embed_documents(
            [meal_embedding_text(meal.name, meal.description, meal.tags)]
        )
    )[0]
    return CuratedMeal(
        name=meal.name,
        meal_type=meal.meal_type,
        description=meal.description,
        ingredients=[ingredient.model_dump() for ingredient in meal.ingredients],
        recipe=meal.recipe,
        tags=meal.tags,
        unverified_ingredients=meal.unverified_ingredients,
        model=meal.model,
        reasoning_trace=[event.model_dump() for event in meal.reasoning_trace],
        approval_status=ApprovalStatus.PENDING,
        embedding=vector,
    )


async def compose_all() -> None:
    """Compose one meal per meal type and store the successes as pending rows."""
    embedder = get_embedder()
    stored = 0
    async with SessionLocal() as session:
        agent = _build_agent(session, embedder)
        for meal_type in MealType:
            row = await _compose_one(agent, embedder, meal_type)
            if row is not None:
                session.add(row)
                stored += 1
                log.info("compose.stored", meal_type=meal_type.value, name=row.name)
        await session.commit()
    log.info("compose.done", stored=stored, requested=len(MealType))


def main() -> None:
    configure_logging()
    asyncio.run(compose_all())


if __name__ == "__main__":
    main()
