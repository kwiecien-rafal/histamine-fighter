"""The moderation side of the admin gate: review, approve, reject curated meals.

Reads the composer's pending output and moves a meal to approved or rejected.
Approval is real safety work, not a rubber stamp: code can only verify the
ingredients the model chose to list, so the human closes the omission gap (the
safety invariant). Approving stamps who signed off and when, for the audit trail.
Never commits; the route layer owns the transaction.
"""

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.enums import ApprovalStatus
from app.models import CuratedMeal

log = structlog.get_logger(__name__)


class MealReviewService:
    """Lists and moderates curated meals. Never commits."""

    default_limit = 50

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_status(
        self, status: ApprovalStatus, *, limit: int | None = None, offset: int = 0
    ) -> list[CuratedMeal]:
        """Return one page of meals in a review state, oldest first.

        Oldest-first plus offset paging means a backlog is worked through FIFO and
        nothing is stranded below the row cap. The id breaks ties between rows that
        share a timestamp (a batch insert shares one). The embedding column is
        heavy and unused by the review queue, so it is deferred, not loaded per row.
        """
        stmt = (
            select(CuratedMeal)
            .where(CuratedMeal.approval_status == status)
            .order_by(CuratedMeal.created_at.asc(), CuratedMeal.id.asc())
            .options(defer(CuratedMeal.embedding))
            .limit(limit or self.default_limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def approve(self, meal_id: UUID, *, actor: str) -> CuratedMeal | None:
        """Approve a meal for the public pool, stamping the actor and time.

        Returns the updated meal, or None when no meal has that id.
        """
        meal = await self._session.get(CuratedMeal, meal_id)
        if meal is None:
            return None
        meal.approval_status = ApprovalStatus.APPROVED
        meal.approved_at = datetime.now(UTC)
        meal.approved_by = actor
        log.info("meal.approved", meal_id=str(meal_id), actor=actor)
        return meal

    async def reject(self, meal_id: UUID) -> CuratedMeal | None:
        """Reject a meal, clearing any prior approval stamp.

        Returns the updated meal, or None when no meal has that id.
        """
        meal = await self._session.get(CuratedMeal, meal_id)
        if meal is None:
            return None
        meal.approval_status = ApprovalStatus.REJECTED
        meal.approved_at = None
        meal.approved_by = None
        log.info("meal.rejected", meal_id=str(meal_id))
        return meal
