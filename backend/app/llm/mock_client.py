import asyncio
from collections.abc import AsyncIterator

from app.llm.base import LLMClient


class MockLLMClient(LLMClient):
    @property
    def model_name(self) -> str:
        return "mock-llm-v0"

    async def complete(self, system: str, user: str) -> str:
        return _canned_response(user)

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        for chunk in _canned_response(user).split(" "):
            await asyncio.sleep(0.02)
            yield chunk + " "


def _canned_response(user: str) -> str:
    dish = user.strip() or "your dish"
    return (
        f"Mock verdict for '{dish}': this looks risky for histamine intolerance. "
        f"A safer swap would be a fresh herb-based version with skinless chicken "
        f"and no aged cheese."
    )
