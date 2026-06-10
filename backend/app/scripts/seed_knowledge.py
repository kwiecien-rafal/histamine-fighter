"""Seed the knowledge_chunks table from the curated knowledge markdown.

Schema lives in Alembic migrations; this loads and embeds the *content*. Each
document is parsed, chunked, and embedded, then the table is rebuilt from
scratch. A full refresh (delete-all then insert) rather than a per-row upsert,
because chunk boundaries shift when a document is edited — an upsert keyed on
(slug, chunk_index) would leave orphaned chunks behind. The corpus is small, so
re-embedding every run is cheap and keeps the table an exact mirror of the files.

Run it (with the database up and migrations applied):

    uv run --directory backend python -m app.scripts.seed_knowledge
"""

import asyncio
from pathlib import Path

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import configure_logging
from app.db.engine import SessionLocal
from app.embeddings import Embedder, get_embedder
from app.knowledge.chunking import ParsedDocument, parse_document
from app.models import KnowledgeChunk, LearnQueryCache

log = structlog.get_logger()

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "seed_data" / "knowledge"


def load_documents(directory: Path) -> list[ParsedDocument]:
    """Parse every knowledge document in the directory, failing loudly on bad data.

    README.md is the directory's own docs, not a corpus document, so it is skipped.
    """
    paths = sorted(p for p in directory.glob("*.md") if p.name.lower() != "readme.md")
    documents: list[ParsedDocument] = []
    for path in paths:
        try:
            documents.append(parse_document(path.read_text(encoding="utf-8")))
        except ValueError as exc:  # includes pydantic's ValidationError
            raise ValueError(f"{path.name}: {exc}") from exc
    slugs = [document.front_matter.slug for document in documents]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise ValueError(f"Duplicate document slugs: {', '.join(duplicates)}")
    return documents


async def _build_chunks(
    embedder: Embedder, documents: list[ParsedDocument]
) -> list[KnowledgeChunk]:
    """Embed each document's chunks and turn them into ORM rows."""
    rows: list[KnowledgeChunk] = []
    for document in documents:
        meta = document.front_matter
        vectors = await embedder.embed_documents([chunk.content for chunk in document.chunks])
        for chunk, vector in zip(document.chunks, vectors, strict=True):
            rows.append(
                KnowledgeChunk(
                    slug=meta.slug,
                    title=meta.title,
                    source=meta.source,
                    topic=meta.topic,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    embedding=vector,
                )
            )
    return rows


async def refresh(session: AsyncSession, rows: list[KnowledgeChunk]) -> None:
    """Replace the whole table with the freshly embedded rows.

    Cached Learn answers cite the old corpus, so they are dropped with it.
    """
    await session.execute(delete(KnowledgeChunk))
    await session.execute(delete(LearnQueryCache))
    session.add_all(rows)


async def seed() -> None:
    """Load the corpus, embed it, and rebuild the table, logging counts."""
    documents = load_documents(KNOWLEDGE_DIR)
    if not documents:
        log.warning("seed.knowledge.empty", directory=str(KNOWLEDGE_DIR))
        return
    rows = await _build_chunks(get_embedder(), documents)
    async with SessionLocal() as session:
        await refresh(session, rows)
        await session.commit()
    log.info("seed.knowledge.done", documents=len(documents), chunks=len(rows))


def main() -> None:
    configure_logging()
    asyncio.run(seed())


if __name__ == "__main__":
    main()
