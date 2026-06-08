from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.llm.langchain_factory import ChatModel

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(filename: str) -> str:
    """Read an agent prompt from ``app/agents/prompts`` by file name."""
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


class BaseAgent(ABC):
    """Shared base for the LLM agents (CLAUDE §8).

    Holds the resolved :class:`ChatModel` — the single LLM seam, so an agent never
    builds a client of its own — and exposes the model name for the transparency
    badge. Each agent implements its own typed ``run``; ``stream`` is declared here
    so the SSE contract cannot be silently dropped by a subclass (an agent that
    has not implemented streaming yet must say so explicitly).
    """

    def __init__(self, chat: ChatModel) -> None:
        self._chat = chat

    @property
    def model_name(self) -> str:
        return self._chat.model_name

    @abstractmethod
    def stream(self, **kwargs: Any) -> AsyncIterator[str]:
        """Stream the agent's answer as SSE text chunks."""
        ...
