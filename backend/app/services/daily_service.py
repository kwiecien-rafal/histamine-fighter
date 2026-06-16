"""The daily board: read it for the public page, moderate it for the admin.

``board_for`` decides locked vs. revealed from the clock and approval state; the
review methods mirror :class:`~app.services.meal_review_service.MealReviewService`
on the daily table. Approval is real safety work, not a rubber stamp: code can only
verify the ingredients the composer chose to list, so the human closes the omission
gap before a meal reaches the public board (the safety invariant). Never commits;
the route layer owns the transaction.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ApprovalStatus, MealType
from app.models import DailySuggestion
from app.schemas.daily import DailyMealCard, DailyMealContent, LockedBoard, RevealedBoard
from app.schemas.meal import TraceEvent

log = structlog.get_logger(__name__)

# Cards and the replayed trace follow the natural meal order, not the alphabetical
# order a SQL sort on the enum value would give.
_MEAL_ORDER = {meal_type: index for index, meal_type in enumerate(MealType)}


class DailyService:
    """Reads and moderates the daily board. Never commits."""

    review_limit = 50

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def board_for(self, on: date, *, now: datetime) -> LockedBoard | RevealedBoard:
        """Return the board for a date, locked or revealed.

        Revealed once ``reveal_at`` has passed and the day has at least one
        approved suggestion; the revealed cards are exactly the approved ones, so a
        single rejected meal drops out instead of locking the whole board. Locked
        otherwise, carrying the shared reveal time for the countdown (null when no
        board is scheduled yet).
        """
        rows = await self._for_date(on)
        reveal_at = min((row.reveal_at for row in rows), default=None)
        approved = [row for row in rows if row.approval_status is ApprovalStatus.APPROVED]
        if not approved or reveal_at is None or now < reveal_at:
            return LockedBoard(date=on, reveal_at=reveal_at)

        ordered = sorted(approved, key=lambda row: _MEAL_ORDER[row.meal_type])
        return RevealedBoard(
            date=on,
            model=ordered[0].model,
            meals=[_to_card(row) for row in ordered],
            trace=[
                TraceEvent.model_validate(event) for row in ordered for event in row.reasoning_trace
            ],
        )

    async def list_for_review(
        self, status: ApprovalStatus, *, limit: int | None = None
    ) -> list[DailySuggestion]:
        """Return suggestions in one review state, soonest reveal date first."""
        stmt = (
            select(DailySuggestion)
            .where(DailySuggestion.approval_status == status)
            .order_by(DailySuggestion.suggestion_date, DailySuggestion.created_at)
            .limit(limit or self.review_limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def approve(self, suggestion_id: UUID, *, actor: str) -> DailySuggestion | None:
        """Approve a suggestion for the public board, stamping the actor and time.

        Returns the updated row, or None when no suggestion has that id.
        """
        suggestion = await self._session.get(DailySuggestion, suggestion_id)
        if suggestion is None:
            return None
        suggestion.approval_status = ApprovalStatus.APPROVED
        suggestion.approved_at = datetime.now(UTC)
        suggestion.approved_by = actor
        log.info("daily.approved", suggestion_id=str(suggestion_id), actor=actor)
        return suggestion

    async def reject(self, suggestion_id: UUID) -> DailySuggestion | None:
        """Reject a suggestion, clearing any prior approval stamp.

        Returns the updated row, or None when no suggestion has that id.
        """
        suggestion = await self._session.get(DailySuggestion, suggestion_id)
        if suggestion is None:
            return None
        suggestion.approval_status = ApprovalStatus.REJECTED
        suggestion.approved_at = None
        suggestion.approved_by = None
        log.info("daily.rejected", suggestion_id=str(suggestion_id))
        return suggestion

    async def _for_date(self, on: date) -> list[DailySuggestion]:
        stmt = select(DailySuggestion).where(DailySuggestion.suggestion_date == on)
        return list((await self._session.execute(stmt)).scalars().all())


def _to_card(row: DailySuggestion) -> DailyMealCard:
    content = DailyMealContent.model_validate(row.content)
    return DailyMealCard(meal_type=row.meal_type, **content.model_dump())
