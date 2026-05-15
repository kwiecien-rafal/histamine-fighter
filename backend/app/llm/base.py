from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class LLMClient(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    async def complete(self, system: str, user: str) -> str: ...

    @abstractmethod
    def stream(self, system: str, user: str) -> AsyncIterator[str]: ...
