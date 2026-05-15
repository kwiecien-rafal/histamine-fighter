from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.llm.base import LLMClient

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class BaseAgent(ABC):
    prompt_file: str

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    @property
    def system_prompt(self) -> str:
        return (_PROMPTS_DIR / self.prompt_file).read_text(encoding="utf-8")

    @abstractmethod
    async def run(self, **kwargs: Any) -> dict[str, Any]: ...

    @abstractmethod
    def stream(self, **kwargs: Any) -> AsyncIterator[str]: ...
