"""Deterministic test doubles that avoid loading the real embedding model."""

import hashlib
import math
import re

from app.embeddings import EMBEDDING_DIM
from app.embeddings.base import Embedder

_TOKEN = re.compile(r"[a-z0-9]+")


class FakeEmbedder(Embedder):
    """A bag-of-words embedder: hashes each word into a dimension, L2-normalizes.

    Deterministic and offline, so the fast suite never downloads a model. Cosine
    similarity tracks word overlap, which is enough to exercise retrieval order.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "fake/deterministic"

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest) % self._dim] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector
