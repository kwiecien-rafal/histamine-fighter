"""Live composer stream for the admin 'generate now' trigger.

The composer is expensive and normally runs offline (the cron writes the board);
this is the honest live demo, where an admin watches the agent compose one meal in
real time. It owns a database session for the life of the stream rather than the
request-scoped one, because a streaming response outlives the request that started
it. The result is not persisted: the nightly job is the production path.
"""

from collections.abc import AsyncIterator

from app.agents.composer import ComposerAgent
from app.db.engine import SessionLocal
from app.embeddings import Embedder
from app.enums import MealType
from app.llm.langchain_factory import ChatModel
from app.services.ingredient_service import IngredientService
from app.services.meal_service import MealService


class ComposerStreamer:
    """Builds a composer over a stream-scoped session and yields its trace JSON."""

    def __init__(self, chat: ChatModel, embedder: Embedder) -> None:
        self._chat = chat
        self._embedder = embedder

    async def stream(self, meal_type: MealType) -> AsyncIterator[str]:
        """Yield the composer's trace, one JSON line per step, ending with the meal."""
        async with SessionLocal() as session:
            agent = ComposerAgent(
                chat=self._chat,
                ingredient_service=IngredientService(session),
                meal_service=MealService(session, self._embedder),
            )
            async for chunk in agent.stream(meal_type):
                yield chunk
