"""Request and response schemas for the admin gate."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.enums import ApprovalStatus, MealType
from app.schemas.meal import ProposedIngredient, TraceEvent

# Generous bounds: just enough to reject absurd payloads. A password over bcrypt's
# 72-byte limit is allowed through and simply fails verification, never matching a
# stored hash, so login stays a clean 401 rather than a 500.
MAX_EMAIL_CHARS = 320
MAX_PASSWORD_CHARS = 128


class AdminLoginRequest(BaseModel):
    """Admin credentials exchanged for an access token."""

    email: str = Field(min_length=1, max_length=MAX_EMAIL_CHARS)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)


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
    model: str = Field(description="Which model composed the meal.")
    reasoning_trace: list[TraceEvent]
    approval_status: ApprovalStatus
    approved_at: datetime | None
    approved_by: str | None
    created_at: datetime
