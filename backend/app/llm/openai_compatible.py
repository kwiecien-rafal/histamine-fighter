import json
from collections.abc import AsyncIterator

import httpx

from app.llm.base import LLMClient

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)


class OpenAICompatibleClient(LLMClient):
    """LLMClient for any endpoint speaking the OpenAI chat-completions API.

    A single httpx-based implementation (no vendor SDK) that serves every
    OpenAI-compatible provider — OpenAI itself, OpenRouter, and the Modal
    inference server — by varying ``base_url`` and ``label``. ``label`` only
    affects :attr:`model_name`, which the frontend renders as a transparency
    badge (e.g. ``openai/gpt-4o-mini``).
    """

    def __init__(self, *, base_url: str, api_key: str, model: str, label: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._label = label

    @property
    def model_name(self) -> str:
        return f"{self._label}/{self._model}"

    async def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "messages": _messages(system, user),
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=self._auth_headers(),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(f"{self._label} request failed: {exc}") from exc

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"{self._label} response contained no choices")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise RuntimeError(f"{self._label} response missing message content")
        return content

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "messages": _messages(system, user),
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=self._auth_headers(),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        piece = _parse_sse_delta(line)
                        if piece:
                            yield piece
            except httpx.HTTPError as exc:
                raise RuntimeError(f"{self._label} stream failed: {exc}") from exc

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_sse_delta(line: str) -> str:
    """Extract the incremental text from one OpenAI streaming SSE line.

    Returns an empty string for keep-alives, the terminal ``[DONE]`` marker,
    and chunks that carry no content delta.
    """
    if not line.startswith("data:"):
        return ""
    data = line[len("data:") :].strip()
    if not data or data == "[DONE]":
        return ""
    chunk = json.loads(data)
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    return choices[0].get("delta", {}).get("content", "") or ""
