"""Local ONNX embeddings via fastembed — no torch, no API key, no service.

fastembed is synchronous, so each call runs in a worker thread to stay off the
event loop. The model is downloaded and loaded on construction, which is why the
embedder is built once behind a process-wide singleton (see ``get_embedder``).
"""

import asyncio

from fastembed import TextEmbedding

from app.embeddings.base import Embedder

_DIMENSIONS: dict[str, int] = {"BAAI/bge-small-en-v1.5": 384}


class FastEmbedEmbedder(Embedder):
    def __init__(self, model_name: str) -> None:
        dimension = _DIMENSIONS.get(model_name)
        if dimension is None:
            raise ValueError(
                f"Unknown fastembed model {model_name!r}; supported: "
                f"{', '.join(sorted(_DIMENSIONS))}. A new model registers its "
                "dimension in _DIMENSIONS."
            )
        self._model_name = model_name
        self._dimension = dimension
        self._model = TextEmbedding(model_name=model_name)

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed, texts, False)

    async def embed_query(self, text: str) -> list[float]:
        return (await asyncio.to_thread(self._embed, [text], True))[0]

    def _embed(self, texts: list[str], as_query: bool) -> list[list[float]]:
        vectors = self._model.query_embed(texts) if as_query else self._model.embed(texts)
        return [vector.tolist() for vector in vectors]
