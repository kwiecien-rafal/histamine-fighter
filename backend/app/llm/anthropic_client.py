from collections.abc import AsyncIterator
from typing import cast

from anthropic import AnthropicError, AsyncAnthropic
from anthropic.types import TextBlock, ToolParam, ToolUseBlock
from pydantic import BaseModel

from app.llm.base import LLMClient
from app.llm.structured import StructuredOutputError, parse_obj

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

    async def generate_structured[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT]
    ) -> ModelT:
        # Anthropic has no response_format. To force a shape, expose one tool
        # whose input_schema is our schema and require that tool. Its input is
        # the structured result.
        tool = cast(
            ToolParam,
            {
                "name": "format_response",
                "description": f"Return the result as a {schema.__name__} object.",
                "input_schema": schema.model_json_schema(),
            },
        )
        async with AsyncAnthropic(api_key=self._api_key) as client:
            try:
                message = await client.messages.create(
                    model=self._model,
                    max_tokens=_MAX_TOKENS,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": "format_response"},
                )
            except AnthropicError as exc:
                raise RuntimeError(f"anthropic request failed: {exc}") from exc
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                return parse_obj(block.input, schema, "anthropic")
        raise StructuredOutputError("anthropic returned no tool_use block")

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
