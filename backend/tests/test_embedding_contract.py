"""The stored embedding width is frozen into the pgvector columns.

curated_meals.embedding and knowledge_chunks.embedding are Vector(384) in the
applied migrations, and every query vector is embedded at the same width.
EMBEDDING_DIM must therefore stay 384: changing it is a re-embed migration of
both corpora, not a config edit. This pins the value that would otherwise only
fail at insert time, deep inside a request.
"""

from app.embeddings import EMBEDDING_DIM


def test_embedding_dim_is_frozen_at_the_migrated_width() -> None:
    assert EMBEDDING_DIM == 384
