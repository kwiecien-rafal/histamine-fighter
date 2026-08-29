"""The embedding seam.

Unlike the LLM provider (swappable per request), the embedding model is fixed
for the whole corpus: stored vectors and query vectors must come from the same
model, and its dimension is baked into the pgvector column. So an ``Embedder`` is
chosen once at startup, not per request.

The two methods exist because retrieval is asymmetric: ``embed_query`` may apply a
model-specific query instruction (bge does) that ``embed_documents`` must not.
"""

from abc import ABC, abstractmethod


class Embedder(ABC):
    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed stored passages (the documents to be retrieved)."""
        ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query."""
        ...
