from collections.abc import AsyncIterator
from typing import Any

from app.agents.base import BaseAgent


class DishLookupAgent(BaseAgent):
    prompt_file = "dish_lookup.md"

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        dish = str(kwargs["dish"])
        text = await self.llm.complete(self.system_prompt, dish)
        return {
            "dish": dish,
            "verdict": "avoid",
            "explanation": text,
            "model": self.llm.model_name,
        }

    async def stream(self, **kwargs: Any) -> AsyncIterator[str]:
        dish = str(kwargs["dish"])
        async for chunk in self.llm.stream(self.system_prompt, dish):
            yield chunk
