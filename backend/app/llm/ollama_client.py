import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel

from app.llm.base import LLMClient
from app.llm.structured import parse_json

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)


class OllamaClient(LLMClient):
    """LLMClient backed by a local Ollama server's REST API.

    Talks to Ollama's `/api/chat` endpoint directly with httpx so we don't
    pull in the Ollama Python SDK. The endpoint accepts a list of chat
    messages and returns either a single JSON body or a stream of newline-
    delimited JSON chunks depending on the `stream` flag.
    """

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    @property
    def model_name(self) -> str:
        return f"ollama/{self._model}"

    async def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "messages": _messages(system, user),
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            try:
                response = await client.post(f"{self._base_url}/api/chat", json=payload)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Ollama request failed: {exc}") from exc

        data = response.json()
        content = data.get("message", {}).get("content", "")
        if not isinstance(content, str):
            raise RuntimeError("Ollama response missing 'message.content' string")
        return content

    async def generate_structured[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT]
    ) -> ModelT:
        # Ollama 0.5+ constrains decoding to the JSON Schema given in `format`.
        # temperature 0 keeps the structured output stable.
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _messages(system, user),
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"temperature": 0},
        }
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            try:
                response = await client.post(f"{self._base_url}/api/chat", json=payload)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Ollama request failed: {exc}") from exc

        data = response.json()
        content = data.get("message", {}).get("content", "")
        if not isinstance(content, str):
            raise RuntimeError("Ollama response missing 'message.content' string")
        return parse_json(content, schema, "ollama")

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "messages": _messages(system, user),
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            try:
                async with client.stream(
                    "POST", f"{self._base_url}/api/chat", json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        piece = chunk.get("message", {}).get("content", "")
                        if piece:
                            yield piece
                        if chunk.get("done"):
                            break
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Ollama stream failed: {exc}") from exc


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
