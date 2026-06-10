"""Smoke test for the real embedding backend (marked: downloads the model).

Mechanical only — it proves the embedder runs and is stable. Retrieval relevance
is evaluated for the knowledge layer (Phase 2), not here.
"""

import pytest

from app.embeddings import EMBEDDING_DIM, get_embedder
from app.embeddings.fastembed_embedder import FastEmbedEmbedder


def test_unknown_model_name_is_a_clear_config_error() -> None:
    """Fails on the dimension registry, before any model download is attempted."""
    with pytest.raises(ValueError, match="Unknown fastembed model 'no/such-model'"):
        FastEmbedEmbedder("no/such-model")


@pytest.mark.embeddings
async def test_fastembed_produces_stable_dimensioned_vectors() -> None:
    embedder = get_embedder()
    assert embedder.dimension == EMBEDDING_DIM

    docs = await embedder.embed_documents(
        ["histamine is a biogenic amine", "fermented foods are often high in histamine"]
    )
    query = await embedder.embed_query("which foods are high in histamine")

    assert [len(vector) for vector in docs] == [EMBEDDING_DIM, EMBEDDING_DIM]
    assert len(query) == EMBEDDING_DIM
    # Deterministic — the same text must embed identically across calls.
    again = await embedder.embed_documents(["histamine is a biogenic amine"])
    assert again[0] == docs[0]
