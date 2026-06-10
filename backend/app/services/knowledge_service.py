"""Retrieval over the embedded knowledge corpus (the RAG read side).

``search`` embeds the query and returns the nearest chunks by cosine similarity,
each carrying its document's citation. Matches below a minimum similarity are
dropped, so an off-topic query returns nothing rather than the k least-unrelated
chunks — "no relevant context" must be distinguishable from "weak context". An
exact distance scan is used (no ANN index) — the corpus is small, so it is both
faster and exact. The embedder is injected so a test can pass a deterministic
stand-in without loading the model.
"""

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import Embedder
from app.models import KnowledgeChunk
from app.schemas.learn import MAX_QUESTION_LENGTH, ArticleSummary

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class KnowledgeMatch:
    """A retrieved chunk and its cosine similarity to the query (1.0 = identical)."""

    chunk: KnowledgeChunk
    similarity: float


class KnowledgeService:
    """Reads the knowledge corpus by vector similarity. Never commits."""

    default_k = 5
    # bge-small cosine similarities sit in a narrow, high band: on this corpus,
    # on-topic questions score ~0.85+ while off-topic text lands around 0.6–0.7.
    # 0.75 separates the two with margin on both sides. The floor is tied to the
    # embedding model — re-tune it (via the retrieval eval) if the model changes.
    default_min_similarity = 0.75

    def __init__(
        self,
        session: AsyncSession,
        embedder: Embedder,
        *,
        min_similarity: float | None = None,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._min_similarity = (
            self.default_min_similarity if min_similarity is None else min_similarity
        )

    async def search(self, query: str, k: int | None = None) -> list[KnowledgeMatch]:
        """Return the k most similar chunks above the similarity floor, best first.

        An empty list means nothing relevant was found (or the query was empty);
        the caller must treat that as "no context", not as an answer. Invalid
        input — an over-long query or a non-positive k — raises ValueError
        instead, so a caller's bug never masquerades as "no match".
        """
        if k is not None and k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        text = query.strip()
        if len(text) > MAX_QUESTION_LENGTH:
            raise ValueError(f"query exceeds {MAX_QUESTION_LENGTH} characters: got {len(text)}")
        if not text:
            return []
        limit = self.default_k if k is None else k

        vector = await self._embedder.embed_query(text)
        distance = KnowledgeChunk.embedding.cosine_distance(vector)
        stmt = select(KnowledgeChunk, distance.label("distance")).order_by(distance).limit(limit)
        rows = (await self._session.execute(stmt)).all()
        candidates = [KnowledgeMatch(chunk, 1.0 - float(dist)) for chunk, dist in rows]
        matches = [match for match in candidates if match.similarity >= self._min_similarity]
        log.debug(
            "knowledge.search",
            query=text[:80],
            retrieved=len(candidates),
            kept=len(matches),
            top=candidates[0].similarity if candidates else None,
        )
        return matches

    async def topics(self) -> list[ArticleSummary]:
        """List the corpus documents (one per slug) for the articles index."""
        stmt = (
            select(KnowledgeChunk.slug, KnowledgeChunk.title, KnowledgeChunk.topic)
            .distinct()
            .order_by(KnowledgeChunk.topic, KnowledgeChunk.title)
        )
        rows = (await self._session.execute(stmt)).all()
        return [ArticleSummary(slug=slug, title=title, topic=topic) for slug, title, topic in rows]
