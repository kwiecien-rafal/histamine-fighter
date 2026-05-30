from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from pydantic import BaseModel


class LLMClient(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    async def complete(self, system: str, user: str) -> str: ...

    @abstractmethod
    def stream(self, system: str, user: str) -> AsyncIterator[str]: ...

    @abstractmethod
    async def generate_structured[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT]
    ) -> ModelT:
        """Return an instance of ``schema``, enforced by the model.

        Each provider uses its own native structured-output feature (OpenAI and
        vLLM response_format, Anthropic tool use, Gemini response_schema, Ollama
        format), so the model is held to the schema while it generates rather
        than just asked to follow it. Implementations validate the result and
        raise StructuredOutputError if it does not match.
        """
        ...
