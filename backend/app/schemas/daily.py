"""Schemas for the daily board: the stored meal content and the public payloads.

A board is either ``locked`` (before its reveal time, or not yet approved) or
``revealed`` (the meal cards, each carrying its own model and replayable trace). The
two share the ``status`` discriminator so the frontend can switch on one field.
No verdict field travels on a card: a daily meal cleared the index when composed
and was approved by an admin, so membership is the signal; the client derives its
badge from the card's ``cautioned_ingredients``.
"""

import datetime as dt
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.meal import CautionedIngredient, ProposedIngredient, PublicMealView
from app.schemas.usage import LLMUsage


class DailyMealContent(BaseModel):
    """The composed meal as stored in a suggestion's JSONB ``content`` column.

    ``unverified_ingredients`` rides along for the admin review queue; it is
    dropped from the public card, which only ever shows approved meals.
    ``cautioned_ingredients`` reaches the card: which ingredients to moderate is
    guidance the visitor needs, not review-queue context. Both default empty so
    rows stored before the fields existed still validate.
    """

    name: str
    description: str
    ingredients: list[ProposedIngredient]
    recipe: list[str] | None
    tags: list[str]
    unverified_ingredients: list[str] = Field(default_factory=list)
    cautioned_ingredients: list[CautionedIngredient] = Field(default_factory=list)


class DailyMealCard(PublicMealView):
    """One revealed meal on the board: the shared public view plus the suggestion id.

    The board offers a per-card "watch how it was composed" replay off the inherited
    ``trace`` instead of one premiere up front. The ``id`` is what a signed-in
    visitor's save references; it only ever surfaces here once the board is revealed.
    """

    id: UUID


class LockedBoard(BaseModel):
    """The board before it unlocks: a countdown target, no meals disclosed.

    ``reveal_at`` is null when no board has been scheduled for the date yet, so
    the frontend can distinguish "not ready" from "counting down".
    """

    status: Literal["locked"] = "locked"
    date: dt.date
    reveal_at: dt.datetime | None = None


class RevealedBoard(BaseModel):
    """The unlocked board: the day's approved meals, each carrying its own trace."""

    status: Literal["revealed"] = "revealed"
    date: dt.date
    model: str = Field(
        description="The board's representative model, used to price the aggregate cost. "
        "Per-meal attribution lives on each card's ``model``."
    )
    meals: list[DailyMealCard]
    usage: LLMUsage = Field(
        default_factory=LLMUsage,
        description="Total token usage of composing the day's meals.",
    )


# Discriminated on ``status`` so clients and the OpenAPI schema branch on one field.
DailyBoard = Annotated[LockedBoard | RevealedBoard, Field(discriminator="status")]
