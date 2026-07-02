"""Request and response schemas for the admin gate."""

import datetime as dt
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import ApprovalStatus, MealType, Role
from app.llm.providers import Provider
from app.schemas.daily import DailyMealContent
from app.schemas.meal import (
    MAX_DESCRIPTION_CHARS,
    MAX_DISH_CHARS,
    ProposedIngredient,
    TraceEvent,
    normalize_dish_text,
    normalize_ingredients,
    normalize_recipe,
    normalize_tags,
)
from app.schemas.usage import LLMUsage

# Generous bounds: just enough to reject absurd payloads. A password over bcrypt's
# 72-byte limit is allowed through and simply fails verification, never matching a
# stored hash, so login stays a clean 401 rather than a 500.
MAX_EMAIL_CHARS = 320
MAX_PASSWORD_CHARS = 128


class AdminLoginRequest(BaseModel):
    """Admin credentials exchanged for an access token."""

    email: str = Field(min_length=1, max_length=MAX_EMAIL_CHARS)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)


class ComposeRequest(BaseModel):
    """Which meal slot the admin wants the live composer to compose."""

    meal_type: MealType


class ComposeDailyRequest(BaseModel):
    """A daily compose-and-save request: which slot, which date, whether to replace.

    ``replace`` must be set explicitly to overwrite a slot already holding a pending or
    approved suggestion, so the destructive intent lives in the request itself, not only
    in a UI confirm. The route bounds the date to the manual-queue window, a check kept
    off the schema so request validation stays free of wall-clock and config.
    """

    meal_type: MealType
    date: dt.date
    replace: bool = False


class ComposeSettingsUpdate(BaseModel):
    """An admin's choice of composer provider and model; the key stays in the env."""

    provider: Provider
    model: str | None = None


class ComposeSettingsRead(BaseModel):
    """The current composer setting plus the providers an admin may switch to."""

    provider: str | None
    model: str | None
    available_providers: list[Provider]


class AuthUser(BaseModel):
    """The signed-in user as the SPA sees it: enough to gate the UI, no token."""

    model_config = ConfigDict(from_attributes=True)

    email: str
    role: Role


class AdminMealRead(BaseModel):
    """One curated meal as the review queue shows it.

    Carries the full ingredient list and the composer's reasoning trace so the
    admin reviews what the agent actually did, rather than approving a bare title.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    meal_type: MealType
    description: str
    ingredients: list[ProposedIngredient]
    recipe: list[str] | None
    tags: list[str]
    unverified_ingredients: list[str]
    model: str = Field(description="Which model composed the meal.")
    usage: LLMUsage | None = Field(description="Token usage of the composition, if recorded.")
    reasoning_trace: list[TraceEvent]
    approval_status: ApprovalStatus
    approved_at: datetime | None
    approved_by: str | None
    created_at: datetime


class AdminDailyRead(BaseModel):
    """One daily suggestion as the review queue shows it.

    Carries the full meal content and the recorded trace so the admin reviews
    what the agent actually composed before it can reveal on the public board.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    date: dt.date = Field(validation_alias="suggestion_date")
    meal_type: MealType
    content: DailyMealContent
    model: str = Field(description="Which model composed the meal.")
    usage: LLMUsage | None = Field(description="Token usage of the composition, if recorded.")
    reasoning_trace: list[TraceEvent]
    reveal_at: datetime
    approval_status: ApprovalStatus
    approved_at: datetime | None
    approved_by: str | None
    created_at: datetime


class QueuedDay(BaseModel):
    """One upcoming date in the daily queue, with its slots grouped for the admin view.

    Server-side grouping so the UI can pick a default generate date and warn when an
    upcoming day is not yet fully approved. ``missing_meal_types`` are the slots not yet
    composed for the date.
    """

    date: dt.date
    slots: list[AdminDailyRead]
    missing_meal_types: list[MealType]
    pending_count: int
    approved_count: int


def _ingredient_pair(item: object) -> tuple[str, str | None]:
    """Pull a (name, category) pair from a raw edit ingredient for shared normalization."""
    if isinstance(item, ProposedIngredient):
        return item.name, item.category
    if isinstance(item, dict):
        name = item.get("name")
        category = item.get("category")
        return (name if isinstance(name, str) else ""), (
            category if isinstance(category, str) else None
        )
    return "", None


class MealEditFields(BaseModel):
    """The admin-editable surface of a composed meal, shaped exactly as the composer shapes it.

    Allowlists exactly the five content fields, so the server-owned approval, model,
    usage, trace, and id can never be set from an edit body (anti-mass-assignment). Each
    field runs the composer's own normalization, so an edit can only store a meal the
    composer could have produced and a freshly composed meal round-trips unchanged. The
    list and length caps truncate rather than reject, so a value past a cap is normalized
    down instead of 422'd; blank name/description and an empty ingredient list still fail,
    since those are not edits the composer would have made.
    """

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    ingredients: list[ProposedIngredient] = Field(min_length=1)
    recipe: list[str] | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> object:
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
        return normalize_ingredients(_ingredient_pair(item) for item in value)

    @field_validator("recipe", mode="before")
    @classmethod
    def _normalize_recipe(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return normalize_recipe(step for step in value if isinstance(step, str))

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return normalize_tags(tag for tag in value if isinstance(tag, str))


class AdminMealCreate(MealEditFields):
    """A hand-written meal an admin authors directly, with no composer in the loop.

    Adds the one field a creation needs that an edit cannot change, the slot the meal
    belongs to, to the shared editable surface. It clears the identical index gate a
    composed meal does, so a manual entry is held to the same safety bar.
    """

    meal_type: MealType


class AdminMealUpdate(MealEditFields):
    """An admin's edit to a pending curated meal."""


class AdminDailyUpdate(MealEditFields):
    """An admin's edit to a pending daily suggestion."""
