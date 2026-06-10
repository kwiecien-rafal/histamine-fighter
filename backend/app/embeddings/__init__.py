"""Embedding seam: one process-wide ``Embedder``, chosen at startup.

``EMBEDDING_DIM`` is the stored-vector width baked into the pgvector columns. It
is pinned to the default model, so changing the embedding model means
re-embedding the corpus and a migration.
"""

import threading

from app.config import settings
from app.embeddings.base import Embedder

__all__ = ["EMBEDDING_DIM", "Embedder", "get_embedder", "warm_up_embedder"]

EMBEDDING_DIM = 384

# A lock, not lru_cache: concurrent first calls must not each load the model.
_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    """The process-wide embedder, built once (the model is expensive to load).

    Construction downloads the model on first ever run, so the app warms this up
    in its lifespan (off the event loop) rather than paying it on a request.
    """
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = _build_embedder()
        return _embedder


def warm_up_embedder() -> Embedder:
    """Build the embedder now so a missing/broken model fails at startup.

    Blocking (model download + ONNX load); call it via ``asyncio.to_thread``
    from async code.
    """
    return get_embedder()


def _build_embedder() -> Embedder:
    # Imported here, not at module top, so reading EMBEDDING_DIM does not pull the
    # ONNX runtime into every importer of this package.
    from app.embeddings.fastembed_embedder import FastEmbedEmbedder

    if settings.embedding_backend == "fastembed":
        embedder: Embedder = FastEmbedEmbedder(settings.embedding_model)
    else:
        raise ValueError(f"Unknown embedding backend: {settings.embedding_backend!r}")
    if embedder.dimension != EMBEDDING_DIM:
        raise ValueError(
            f"Embedding model {embedder.model_name!r} produces "
            f"{embedder.dimension}-dim vectors, but the stored corpus is "
            f"Vector({EMBEDDING_DIM}). Changing dimension requires a migration "
            "and a corpus re-seed, not just a config change."
        )
    return embedder
