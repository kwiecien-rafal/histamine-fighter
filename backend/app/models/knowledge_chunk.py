"""ORM model for embedded chunks of the curated knowledge corpus.

Each row is one chunk of a Learn-hub document, denormalized with its document's
citation metadata so a retrieved chunk carries everything needed to cite it
without a join. The RAG layer (KnowledgeService) reads these by vector similarity.
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.embeddings import EMBEDDING_DIM


class KnowledgeChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One embedded passage of a curated, sourced knowledge document."""

    __tablename__ = "knowledge_chunks"

    slug: Mapped[str]
    title: Mapped[str]
    source: Mapped[str]
    topic: Mapped[str]
    chunk_index: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (
        # The seed re-chunks a whole document on each run; (slug, chunk_index) is
        # the stable identity it upserts/refreshes against.
        UniqueConstraint(
            "slug", "chunk_index", name="uq_knowledge_chunks_slug_chunk_index"
        ),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeChunk {self.slug!r}#{self.chunk_index}>"
