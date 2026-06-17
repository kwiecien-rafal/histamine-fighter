"""Request and response schemas for the admin gate."""

import datetime as dt
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.enums import ApprovalStatus, MealType
from app.schemas.daily import DailyMealContent
from app.schemas.meal import ProposedIngredient, TraceEvent
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


class DailyGenerateRequest(BaseModel):
    """Which meal slot the admin wants the live composer to demonstrate."""

    meal_type: MealType


class TokenResponse(BaseModel):
    """A bearer token the client stores and replays on admin requests."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"


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
