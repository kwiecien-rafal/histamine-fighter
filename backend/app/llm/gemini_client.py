from collections.abc import AsyncIterator

from google import genai
from google.genai import errors, types

from app.llm.base import LLMClient


class GeminiClient(LLMClient):
    """LLMClient backed by Google Gemini via the google-genai SDK.

    Gemini takes the system prompt as a dedicated ``system_instruction`` on
    the request config rather than as a chat turn. The SDK's async surface
    (``client.aio``) is used throughout; its errors are wrapped in
    ``RuntimeError`` to match the other providers.
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    @property
    def model_name(self) -> str:
        return f"gemini/{self._model}"

    async def complete(self, system: str, user: str) -> str:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=user,
                config=types.GenerateContentConfig(system_instruction=system),
            )
        except errors.APIError as exc:
            raise RuntimeError(f"gemini request failed: {exc}") from exc
        return response.text or ""

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=self._model,
                contents=user,
                config=types.GenerateContentConfig(system_instruction=system),
            )
            async for chunk in stream:
                if chunk.text:
                    yield chunk.text
        except errors.APIError as exc:
            raise RuntimeError(f"gemini stream failed: {exc}") from exc
