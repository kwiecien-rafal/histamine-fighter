from collections.abc import AsyncIterator
from typing import Any

from app.agents.base import BaseAgent
from app.schemas.meal import DishLookupResponse, DishVerdict


class DishLookupAgent(BaseAgent):
    prompt_file = "dish_lookup.md"

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        dish = str(kwargs["dish"])
        verdict = await self.llm.generate_structured(self.system_prompt, dish, DishVerdict)
        response = DishLookupResponse(**verdict.model_dump(), model=self.llm.model_name)
        return response.model_dump()

    async def stream(self, **kwargs: Any) -> AsyncIterator[str]:
        dish = str(kwargs["dish"])
        async for chunk in self.llm.stream(self.system_prompt, dish):
            yield chunk
