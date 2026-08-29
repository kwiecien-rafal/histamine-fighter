"""Row factories shared by the server-rendered page tests.

The pages read the same tables as the JSON API, so these build the smallest rows
that still exercise every branch a template has: a recipe, a caution note, and a
trace all render differently from their absence.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import EMBEDDING_DIM
from app.enums import ApprovalStatus, MealType, SafetyLevel, SavedMealTag, SaveSource
from app.models import CuratedMeal, DailySuggestion, KnowledgeChunk, SavedMeal

ZERO_VECTOR = [0.0] * EMBEDDING_DIM
DEFAULT_TRACE = [
    {"kind": "draft", "text": "the model thinking out loud"},
    {"kind": "verify", "text": "All ingredients cleared the index."},
]


async def add_curated_meal(
    session: AsyncSession,
    *,
    name: str = "Courgette ribbon salad",
    meal_type: MealType = MealType.LUNCH,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
    model: str = "fake/test",
    cautioned: list[dict[str, str]] | None = None,
    created_at: datetime | None = None,
) -> CuratedMeal:
    """An approved curated meal carrying a recipe, tags, and a reasoning trace."""
    meal = CuratedMeal(
        name=name,
        meal_type=meal_type,
        description="raw courgette ribbons with olive oil and fresh herbs",
        ingredients=[{"name": "courgette", "category": "vegetable"}],
        recipe=["Peel into ribbons.", "Toss with oil and herbs."],
        tags=["fresh"],
        unverified_ingredients=[],
        cautioned_ingredients=cautioned or [],
        model=model,
        reasoning_trace=DEFAULT_TRACE,
        approval_status=approval_status,
        embedding=ZERO_VECTOR,
    )
    # now() is transaction-scoped in Postgres, so an explicit stamp is the only way
    # to give a batch of rows a deterministic browse order.
    if created_at is not None:
        meal.created_at = created_at
    session.add(meal)
    await session.flush()
    return meal


async def add_daily_suggestion(
    session: AsyncSession,
    *,
    reveal_at: datetime,
    meal_type: MealType = MealType.BREAKFAST,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
    name: str = "Courgette ribbon salad",
) -> DailySuggestion:
    """One board slot, revealed or locked depending on the reveal time passed in."""
    content: dict[str, Any] = {
        "name": name,
        "description": "raw courgette ribbons with olive oil and fresh herbs",
        "ingredients": [{"name": "courgette", "category": "vegetable"}],
        "recipe": ["Peel into ribbons.", "Toss with oil and herbs."],
        "tags": ["fresh"],
        "unverified_ingredients": [],
        "cautioned_ingredients": [],
    }
    row = DailySuggestion(
        suggestion_date=reveal_at.date(),
        meal_type=meal_type,
        content=content,
        model="fake/test",
        reasoning_trace=DEFAULT_TRACE,
        reveal_at=reveal_at,
        approval_status=approval_status,
    )
    session.add(row)
    await session.flush()
    return row


async def add_knowledge_chunk(
    session: AsyncSession,
    *,
    slug: str,
    title: str,
    topic: str,
) -> KnowledgeChunk:
    """One corpus passage, enough for the Learn hub's topic index to list its document."""
    chunk = KnowledgeChunk(
        slug=slug,
        title=title,
        source="SIGHI",
        topic=topic,
        chunk_index=0,
        content="Diamine oxidase breaks down histamine in the gut.",
        embedding=ZERO_VECTOR,
    )
    session.add(chunk)
    await session.flush()
    return chunk


async def add_saved_meal(
    session: AsyncSession,
    *,
    user_id: UUID,
    source: SaveSource = SaveSource.LOOKUP,
    source_key: str | None = None,
    name: str = "Spaghetti with herb sauce",
    meal_type: MealType | None = None,
    verdict: SafetyLevel | None = SafetyLevel.DEPENDS,
    recipe: list[str] | None = None,
    tags: list[str] | None = None,
) -> SavedMeal:
    """One copy on a user's shelf, a dish-check save unless told otherwise."""
    row = SavedMeal(
        user_id=user_id,
        source=source,
        source_key=source_key or str(uuid4()),
        meal_type=meal_type,
        name=name,
        description="courgette ribbons standing in for the pasta",
        ingredients=[{"name": "courgette", "category": "vegetable"}],
        recipe=recipe,
        tags=tags if tags is not None else [SavedMealTag.DISH_CHECK.value],
        cautioned_ingredients=[],
        model="fake/test",
        verdict=verdict,
    )
    session.add(row)
    await session.flush()
    return row
