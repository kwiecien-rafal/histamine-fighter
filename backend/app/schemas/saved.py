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

from pydantic import BaseModel, Field, field_validator

from app.enums import MealType, SafetyLevel, SavedMealTag, SaveSource
from app.schemas.admin import MealEditFields
from app.schemas.meal import (
    MAX_DESCRIPTION_CHARS,
    MAX_DISH_CHARS,
    CautionedIngredient,
    ProposedIngredient,
    normalize_dish_text,
    normalize_ingredients,
)

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
    """

    source: Literal[SaveSource.LOOKUP]
    dish: str = Field(min_length=1, max_length=MAX_DISH_CHARS)
    verdict: SafetyLevel
    description: str = ""
    ingredients: list[ProposedIngredient] = Field(min_length=1)
    model: str = Field(min_length=1, max_length=MAX_MODEL_CHARS)

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


class SavedMealPage(BaseModel):
    """Every saved meal for the signed-in user; capped, so no paging needed."""

    items: list[SavedMealCard]
