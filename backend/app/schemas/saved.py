"""Schemas for per-user saved meals.

The save request is a union discriminated on ``source``: curated and daily saves
send only the source row's id and the server copies the content (so a snapshot of
pool content is always authentic), while a lookup save must carry its content —
normalized through the same helpers the composer uses, so a client can never
store more than an assessment could legitimately hold. The response shapes stay
close to the public meal views the profile renders alongside.
"""

import datetime as dt
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.enums import MealType, SafetyLevel, SavedMealTag, SaveSource
from app.schemas.admin import MealEditFields
from app.schemas.meal import (
    MAX_DESCRIPTION_CHARS,
    MAX_DISH_CHARS,
    CautionedIngredient,
    ProposedIngredient,
    normalize_dish_text,
    normalize_ingredients,
    normalize_recipe,
)
from app.schemas.usage import LLMUsage

# The model name arrives from the client on lookup saves; cap it so the column
# cannot be used as a free-text dump.
MAX_MODEL_CHARS = 100


class SaveByReference(BaseModel):
    """Save a curated meal or a daily suggestion: an id, never client content."""

    source: Literal[SaveSource.CURATED, SaveSource.DAILY]
    source_id: UUID


class SaveFromLookup(BaseModel):
    """Save an assessed dish: the client sends the snapshot, normalized and capped.

    There is no durable server row to copy from, so this content is client-asserted
    by construction; it is stored privately per user and never presented as verified.

    ``lookup_id`` is minted by the client per assessment result and becomes the
    save's ``source_key``. It cannot be server-assigned: a cached assessment is
    shared across users, so a server id would collide where results must stay
    distinct. Keying on it (not the dish name) lets two different results of the
    same-named dish coexist as separate saves.
    """

    source: Literal[SaveSource.LOOKUP]
    lookup_id: UUID
    dish: str = Field(min_length=1, max_length=MAX_DISH_CHARS)
    verdict: SafetyLevel
    description: str = ""
    ingredients: list[ProposedIngredient] = Field(min_length=1)
    model: str = Field(min_length=1, max_length=MAX_MODEL_CHARS)
    # A recipe generated on the result card, riding into the save. Normalized
    # like an edited recipe, so a save can never hold more than an edit could.
    recipe: list[str] | None = None
    recipe_model: str | None = Field(default=None, max_length=MAX_MODEL_CHARS)

    @field_validator("dish", mode="before")
    @classmethod
    def _normalize_dish(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return normalize_dish_text(value, max_chars=MAX_DISH_CHARS)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return normalize_dish_text(value, max_chars=MAX_DESCRIPTION_CHARS)

    @field_validator("ingredients", mode="before")
    @classmethod
    def _normalize_ingredients(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        pairs: list[tuple[str, str | None]] = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("name")
                category = item.get("category")
                pairs.append(
                    (
                        name if isinstance(name, str) else "",
                        category if isinstance(category, str) else None,
                    )
                )
        return normalize_ingredients(pairs)

    @field_validator("recipe", mode="before")
    @classmethod
    def _normalize_recipe(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        # normalize_recipe returns None when every step is blank, so a junk
        # recipe degrades to "no recipe" rather than storing empty steps.
        return normalize_recipe([step for step in value if isinstance(step, str)])

    @model_validator(mode="after")
    def _recipe_model_needs_recipe(self) -> "SaveFromLookup":
        if self.recipe is None:
            self.recipe_model = None
        return self


SaveRequest = Annotated[SaveByReference | SaveFromLookup, Field(discriminator="source")]


class SavedMealUpdate(MealEditFields):
    """A user's edit to their own saved copy.

    The same allowlisted, composer-normalized surface as the admin edit, so a user
    copy can only hold content shaped like a real meal. ``confirm_flagged`` is inert
    here: personal copies pass no index gate, they just lose the verified badge.
    Tags diverge from the free-form admin surface: saved meals only accept the
    closed :class:`SavedMealTag` vocabulary.
    """

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> object:
        # Overrides the composer's 8-tag cap, which would silently drop picks from
        # the 11-value vocabulary. Just trims and drops blanks; the closed set below
        # bounds the count and rejects anything off-vocabulary.
        if not isinstance(value, list):
            return value
        return [tag.strip() for tag in value if isinstance(tag, str) and tag.strip()]

    @field_validator("tags", mode="after")
    @classmethod
    def _restrict_tags(cls, value: list[str]) -> list[str]:
        allowed = {tag.value for tag in SavedMealTag}
        seen: list[str] = []
        for tag in value:
            lowered = tag.casefold()
            if lowered not in allowed:
                raise ValueError(f"Unknown tag {tag!r}; saved meals accept only: {sorted(allowed)}")
            if lowered not in seen:
                seen.append(lowered)
        return seen


class SavedMealCard(BaseModel):
    """One saved meal as the profile grid lists it: lean, with provenance."""

    id: UUID
    source: SaveSource
    source_key: str
    meal_type: MealType | None
    name: str
    description: str
    tags: list[str]
    verdict: SafetyLevel | None
    edited_at: dt.datetime | None
    created_at: dt.datetime
    has_recipe: bool


class SavedMealDetail(SavedMealCard):
    """One saved meal in full, for the profile's edit view."""

    ingredients: list[ProposedIngredient]
    recipe: list[str] | None
    cautioned_ingredients: list[CautionedIngredient]
    model: str
    # Which model wrote the lazily generated recipe; null until one exists.
    recipe_model: str | None


class SavedMealPage(BaseModel):
    """Every saved meal for the signed-in user; capped, so no paging needed."""

    items: list[SavedMealCard]


class SavedRecipeResponse(BaseModel):
    """The saved meal after a recipe request, plus that call's provenance.

    ``recipe_model`` names the model that wrote the steps — persisted on the
    row, so a reload badges the same model. Only a recipe that came with the
    snapshot itself falls back to the save's model. Usage is zero whenever a
    stored recipe is returned unchanged.
    """

    meal: SavedMealDetail
    recipe_model: str
    usage: LLMUsage
