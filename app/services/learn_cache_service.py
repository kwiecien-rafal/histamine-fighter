"""TTL cache for grounded Learn-hub answers (the cost-control side of RAG).

The corpus only changes when the seed script runs (which clears this table), so
a grounded answer is valid for its whole TTL. Keyed by (normalized question,
model): users pick their provider per request, and the transparency badge must
never attribute one model's prose to another. Only grounded answers are cached —
a decline is cheap to recompute and partly a model judgment call, not a fact
worth pinning for days. Never commits; the request session owns the transaction.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.normalization import normalize_question
from app.models import LearnQueryCache
from app.schemas.learn import LearnResponse

log = structlog.get_logger(__name__)


class LearnCacheService:
    """Reads and writes cached grounded answers for the Learn endpoint."""

    def __init__(self, session: AsyncSession, *, ttl_days: int | None = None) -> None:
        self._session = session
        self._ttl = timedelta(days=settings.learn_cache_ttl_days if ttl_days is None else ttl_days)

    async def get(self, question: str, model: str) -> LearnResponse | None:
        """Return the cached answer for this question and model, or None.

        The stored response echoes the question it was cached under; it is
        replaced with the caller's exact phrasing so the response always mirrors
        the request.
        """
        key = normalize_question(question)
        if not key:
            return None
        stmt = select(LearnQueryCache.response).where(
            LearnQueryCache.question_key == key,
            LearnQueryCache.model == model,
            LearnQueryCache.expires_at > datetime.now(UTC),
        )
        payload: dict[str, Any] | None = (await self._session.execute(stmt)).scalar_one_or_none()
        if payload is None:
            return None
        log.info("learn.cache_hit", question=question[:80], model=model)
        return LearnResponse.model_validate({**payload, "question": question})

    async def put(self, question: str, response: LearnResponse) -> None:
        """Upsert a grounded answer and opportunistically drop expired rows."""
        if not response.grounded:
            return
        key = normalize_question(question)
        if not key:
            return
        now = datetime.now(UTC)
        await self._session.execute(
            delete(LearnQueryCache).where(LearnQueryCache.expires_at <= now)
        )
        values = {
            "question_key": key,
            "model": response.model,
            "response": response.model_dump(),
            "expires_at": now + self._ttl,
        }
        stmt = insert(LearnQueryCache).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_learn_query_cache_question_key_model",
            set_={
                "response": stmt.excluded.response,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        await self._session.execute(stmt)
