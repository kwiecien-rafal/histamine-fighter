"""Schemas for the daily board: the stored meal content and the public payloads.

A board is either ``locked`` (before its reveal time, or not yet approved) or
``revealed`` (the meal cards plus the composer's recorded trace to replay). The
two share the ``status`` discriminator so the frontend can switch on one field.
No verdict travels on a card: a daily meal is safe by construction (it cleared
the index when composed) and approved by an admin, so membership is the signal.
"""

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.enums import MealType
from app.schemas.meal import ProposedIngredient, TraceEvent


class DailyMealContent(BaseModel):
    """The composed meal as stored in a suggestion's JSONB ``content`` column.

    ``unverified_ingredients`` rides along for the admin review queue; it is
    dropped from the public card, which only ever shows approved meals.
    """

    name: str
    description: str
    ingredients: list[ProposedIngredient]
    recipe: list[str] | None
    tags: list[str]
    unverified_ingredients: list[str] = Field(default_factory=list)


class DailyMealCard(BaseModel):
    """One revealed meal on the board: its slot plus its stored content."""

    meal_type: MealType
    name: str
    description: str
    ingredients: list[ProposedIngredient]
    recipe: list[str] | None
    tags: list[str]


class LockedBoard(BaseModel):
    """The board before it unlocks: a countdown target, no meals disclosed.

    ``reveal_at`` is null when no board has been scheduled for the date yet, so
    the frontend can distinguish "not ready" from "counting down".
    """

    status: Literal["locked"] = "locked"
    date: dt.date
    reveal_at: dt.datetime | None = None


class RevealedBoard(BaseModel):
    """The unlocked board: the day's approved meals and the trace to replay."""

    status: Literal["revealed"] = "revealed"
    date: dt.date
    model: str = Field(description="Which model composed the day's meals.")
    meals: list[DailyMealCard]
    trace: list[TraceEvent] = Field(
        description="The composer's recorded reasoning across the day's meals, in order."
    )


# Discriminated on ``status`` so clients and the OpenAPI schema branch on one field.
DailyBoard = Annotated[LockedBoard | RevealedBoard, Field(discriminator="status")]
