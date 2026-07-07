"""The recipe agent: preparation steps for a meal whose ingredients are settled.

Called on demand for a saved meal that has no recipe yet. The ingredient list
was assessed (and possibly user-edited) before this agent runs, so the prompt
forbids adding ingredients: an addition would bypass the index check that made
the list trustworthy. The prompt is not trusted to hold, though — like the
composer, code scans the drafted steps for index-avoid terms that are not on
the list, feeds one corrective retry, and fails the generation rather than
persist a smuggled ingredient. Draft steps are normalized through the same
:func:`normalize_recipe` the composer and admin edits use, so a generated
recipe can never hold more than an edited one could.
"""

from collections.abc import AsyncIterator

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.agents.base import BaseAgent, loggable_messages
from app.agents.prompting import load_prompt, render_prompt, strip_region_tags
from app.core.term_match import TermMatcher
from app.llm.errors import LLMInvocationError
from app.llm.langchain_factory import ChatModel
from app.schemas.meal import (
    CautionedIngredient,
    ProposedIngredient,
    RecipeDraft,
    RecipeGeneration,
    normalize_recipe,
)
from app.services.ingredient_service import IngredientService

log = structlog.get_logger(__name__)

_INVOCATION_ERROR = (
    "The language model failed to write the recipe. "
    "If you selected a custom model, make sure it supports structured output."
)
_FLAGGED_ERROR = (
    "The language model kept writing a high-histamine ingredient into the steps, "
    "so no recipe was saved. Try again."
)

# The single user-content region; every value is stripped against it so none
# can forge the delimiter.
_RECIPE_TAGS = ("meal",)


def _ingredient_line(item: ProposedIngredient) -> str:
    return f"{item.name} ({item.category})" if item.category else item.name


class RecipeAgent(BaseAgent):
    """Writes ordered preparation steps for an already-assessed meal."""

    _invocation_error = _INVOCATION_ERROR

    def __init__(self, chat: ChatModel, service: IngredientService) -> None:
        super().__init__(chat)
        self._service = service
        self._system_prompt = render_prompt(
            load_prompt("recipe/system"),
            "recipe/system",
            input_tag="<meal>",
        )
        self._user_template = load_prompt("recipe/user")

    def stream(self, *args: object, **kwargs: object) -> AsyncIterator[str]:
        # Declared, not omitted, so the streaming contract stays explicit; deferred.
        raise NotImplementedError("Streaming recipe generation is not implemented yet.")

    async def run(
        self,
        name: str,
        description: str,
        ingredients: list[ProposedIngredient],
        cautions: list[CautionedIngredient],
    ) -> RecipeGeneration:
        """Write and normalize the recipe steps for one meal.

        The cautions are the index's own moderation notes for kept ingredients
        ("fresh only"), passed through so the steps can honour them. A draft
        whose steps all normalize away is a failed generation, not an empty
        recipe, and raises the agent's domain error.
        """
        self._begin_usage()
        caution_lines = (
            "; ".join(f"{item.name} — {item.note}" for item in cautions) if cautions else "None."
        )
        messages: list[BaseMessage] = [
            SystemMessage(self._system_prompt),
            HumanMessage(
                render_prompt(
                    self._user_template,
                    "recipe/user",
                    # Name and description are user-editable content; ingredients
                    # and cautions came from stored rows but are stripped too,
                    # defence in depth against a forged region delimiter.
                    name=strip_region_tags(name, _RECIPE_TAGS),
                    description=strip_region_tags(description or "None.", _RECIPE_TAGS),
                    ingredients=strip_region_tags(
                        ", ".join(_ingredient_line(item) for item in ingredients), _RECIPE_TAGS
                    ),
                    cautions=strip_region_tags(caution_lines, _RECIPE_TAGS),
                )
            ),
        ]
        log.debug("recipe.request", messages=loggable_messages(messages))
        draft = await self._structured_invoke(RecipeDraft, messages, step="recipe")
        log.debug("recipe.reply", draft=draft.model_dump())
        steps = self._usable_steps(draft)
        flagged = await self._off_list_risky_terms(steps, ingredients)
        if flagged:
            # One corrective retry: the prompt rule was ignored, so the flags
            # are named explicitly and the model gets a single second chance.
            log.warning("recipe.flagged_terms", flagged=len(flagged), model=self._chat.model_name)
            messages.append(AIMessage(content="\n".join(draft.steps)))
            messages.append(
                HumanMessage(
                    "These steps use ingredients that are not on the list and are "
                    f"high in histamine: {', '.join(flagged)}. Rewrite the steps "
                    "without them, using only the listed ingredients plus water, "
                    "salt and pepper."
                )
            )
            draft = await self._structured_invoke(RecipeDraft, messages, step="recipe_retry")
            steps = self._usable_steps(draft)
            if await self._off_list_risky_terms(steps, ingredients):
                log.warning("recipe.flagged_after_retry", model=self._chat.model_name)
                raise LLMInvocationError(_FLAGGED_ERROR)
        # Counts only: this always-on line carries no user content.
        log.info(
            "recipe.generated",
            drafted=len(draft.steps),
            kept=len(steps),
            model=self._chat.model_name,
        )
        return RecipeGeneration(
            steps=steps,
            model=self._chat.model_name,
            usage=self._collect_usage(),
        )

    def _usable_steps(self, draft: RecipeDraft) -> list[str]:
        steps = normalize_recipe(draft.steps)
        if steps is None:
            log.warning("recipe.empty_draft", model=self._chat.model_name)
            raise LLMInvocationError(self._invocation_error)
        return steps

    async def _off_list_risky_terms(
        self, steps: list[str], ingredients: list[ProposedIngredient]
    ) -> list[str]:
        """Index-avoid terms the steps mention but the ingredient list does not.

        A listed ingredient is the user's own, already-badged call, so terms
        that match a listed name are allowed — per name, not against the joined
        list, so a multi-word term cannot assemble itself from two ingredients.
        """
        matcher = TermMatcher.from_terms(await self._service.avoid_terms())
        allowed = {term for item in ingredients for term in matcher.found_in(item.name)}
        flagged: list[str] = []
        for step in steps:
            for term in matcher.found_in(step):
                if term not in allowed and term not in flagged:
                    flagged.append(term)
        return flagged
