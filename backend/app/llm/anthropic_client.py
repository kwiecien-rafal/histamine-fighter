from collections.abc import AsyncIterator

from anthropic import AnthropicError, AsyncAnthropic
from anthropic.types import TextBlock

from app.llm.base import LLMClient

_MAX_TOKENS = 2048


class AnthropicClient(LLMClient):
    """LLMClient backed by the Anthropic Messages API via the official SDK.

    Anthropic takes the system prompt as a top-level argument rather than a
    message role, and requires an explicit ``max_tokens`` cap. The SDK owns
    transport, retries, and streaming; its errors are wrapped in
    ``RuntimeError`` to match the other providers.
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    @property
    def model_name(self) -> str:
        return f"anthropic/{self._model}"

    async def complete(self, system: str, user: str) -> str:
        async with AsyncAnthropic(api_key=self._api_key) as client:
            try:
                message = await client.messages.create(
                    model=self._model,
                    max_tokens=_MAX_TOKENS,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
            except AnthropicError as exc:
                raise RuntimeError(f"anthropic request failed: {exc}") from exc
        return "".join(b.text for b in message.content if isinstance(b, TextBlock))

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        async with AsyncAnthropic(api_key=self._api_key) as client:
            try:
                async with client.messages.stream(
                    model=self._model,
                    max_tokens=_MAX_TOKENS,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                ) as stream:
                    async for text in stream.text_stream:
                        yield text
            except AnthropicError as exc:
                raise RuntimeError(f"anthropic stream failed: {exc}") from exc
