from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import BaseMessage

from app.llm.langchain_factory import ChatModel


def loggable_messages(messages: Sequence[BaseMessage]) -> list[dict[str, str]]:
    """Messages as role/content pairs for the per-call ``*.request`` debug events.

    Prompts are logged at the invocation boundary — when they are sent, not when
    their templates render — so one request's debug log reads chronologically:
    each model call's ``request`` event, then its reply.
    """
    return [{"role": message.type, "content": str(message.content)} for message in messages]


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
    def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        """Stream the agent's answer as SSE text chunks.

        Declared loose here so each agent can type its own signature (mirroring
        its ``run``) without violating the override contract.
        """
        ...
