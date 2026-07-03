"""The LLM quality judge for composed meals.

One judge, five binary questions, a configurable threshold: the cheapest form of
LLM-as-judge that still cuts junk from the admin review queue. It deliberately is
not a panel: the strongest judge in the pipeline is the human approval every meal
already needs, so this call's only job is sparing the reviewer the obviously bare
or incoherent submissions. Quality only, never safety: the meal it sees has
already cleared the code-owned index gate, and nothing the judge says can admit a
flagged ingredient.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.agents.prompting import load_prompt, render_prompt, strip_region_tags
from app.enums import MealType
from app.llm.langchain_factory import ChatModel
from app.schemas.meal import MealJudgement, ProposedIngredient
from app.schemas.usage import StepUsage

_INVOCATION_ERROR = "The judge model failed while reviewing a meal."


class MealJudgeAgent(BaseAgent):
    """Reviews one composed meal against five binary quality criteria."""

    _invocation_error = _INVOCATION_ERROR

    def __init__(self, chat: ChatModel) -> None:
        super().__init__(chat)
        self._system_prompt = render_prompt(
            load_prompt("judge/system"), "judge/system", input_tag="<meal>"
        )
        self._review_user_template = load_prompt("judge/review_user")

    async def review(
        self,
        meal_type: MealType,
        *,
        name: str,
        description: str,
        ingredients: Sequence[ProposedIngredient],
        recipe: Sequence[str] | None,
        tags: Sequence[str],
    ) -> tuple[MealJudgement, list[StepUsage]]:
        """Judge one meal, returning the verdict and the call's token usage.

        The usage rides back explicitly so the composer can fold it into its own
        tally: the judged meal's cost then includes its reviews, keeping the
        transparency panel honest.
        """
        self._begin_usage()
        judgement = await self._structured_invoke(
            MealJudgement,
            self._messages(meal_type, name, description, ingredients, recipe, tags),
            step="judge",
        )
        return judgement, list(self._calls)

    def _messages(
        self,
        meal_type: MealType,
        name: str,
        description: str,
        ingredients: Sequence[ProposedIngredient],
        recipe: Sequence[str] | None,
        tags: Sequence[str],
    ) -> list[BaseMessage]:
        listed = ", ".join(
            f"{item.name} ({item.category})" if item.category else item.name for item in ingredients
        )
        steps = "\n".join(f"{index}. {step}" for index, step in enumerate(recipe or [], start=1))
        rendered = render_prompt(
            self._review_user_template,
            "judge/review_user",
            meal_type=meal_type.value,
            name=strip_region_tags(name, ("meal",)),
            description=strip_region_tags(description, ("meal",)),
            ingredients=strip_region_tags(listed, ("meal",)),
            recipe=strip_region_tags(steps, ("meal",)) or "(no steps)",
            tags=strip_region_tags(", ".join(tags), ("meal",)) or "(none)",
        )
        return [SystemMessage(self._system_prompt), HumanMessage(rendered)]

    def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        """The judge returns one structured verdict; it has nothing to stream."""
        raise NotImplementedError("MealJudgeAgent does not stream.")
