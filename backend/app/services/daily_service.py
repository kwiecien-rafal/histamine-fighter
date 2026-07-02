"""The daily board: read it for the public page, moderate it for the admin.

``board_for`` decides locked vs. revealed from the clock and approval state; the
review methods mirror :class:`~app.services.meal_review_service.MealReviewService`
on the daily table. Approval is real safety work, not a rubber stamp: code can only
verify the ingredients the composer chose to list, so the human closes the omission
gap before a meal reaches the public board (the safety invariant). Never commits;
the route layer owns the transaction.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import ApprovalStatus, MealType
from app.models import DailySuggestion
from app.schemas.admin import AdminDailyRead, AdminDailyUpdate, QueuedDay
from app.schemas.daily import DailyMealCard, DailyMealContent, LockedBoard, RevealedBoard
from app.schemas.meal import ComposedMeal, TraceEvent, public_trace
from app.schemas.usage import LLMUsage

log = structlog.get_logger(__name__)

# Cards and the replayed trace follow the natural meal order, not the alphabetical
# order a SQL sort on the enum value would give.
_MEAL_ORDER = {meal_type: index for index, meal_type in enumerate(MealType)}


class DailyService:
    """Reads, generates, and moderates the daily board. Never commits."""

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
            usage=_total_usage(ordered),
        )

    async def list_queue(self, *, today: date) -> list[QueuedDay]:
        """Group the upcoming suggestions (today onward) by date for the admin queue.

        Each day carries its slots in natural meal order, the meal types still missing,
        and pending/approved counts, so the UI can pick a default generate date and flag
        an upcoming day that is not yet fully approved.

        Bounded to the furthest date either path can fill (the manual-queue window or the
        cron horizon), so the response stays a fixed size and a cron-composed day past the
        manual window still surfaces for approval.
        """
        horizon = today + timedelta(
            days=max(settings.daily_queue_max_ahead_days, settings.daily_cron_horizon_days)
        )
        stmt = (
            select(DailySuggestion)
            .where(
                DailySuggestion.suggestion_date >= today,
                DailySuggestion.suggestion_date <= horizon,
            )
            .order_by(DailySuggestion.suggestion_date)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        by_date: dict[date, list[DailySuggestion]] = defaultdict(list)
        for row in rows:
            by_date[row.suggestion_date].append(row)

        days: list[QueuedDay] = []
        for day in sorted(by_date):
            slots = sorted(by_date[day], key=lambda row: _MEAL_ORDER[row.meal_type])
            present = {slot.meal_type for slot in slots}
            days.append(
                QueuedDay(
                    date=day,
                    slots=[AdminDailyRead.model_validate(slot) for slot in slots],
                    missing_meal_types=[mt for mt in MealType if mt not in present],
                    pending_count=sum(
                        1 for slot in slots if slot.approval_status is ApprovalStatus.PENDING
                    ),
                    approved_count=sum(
                        1 for slot in slots if slot.approval_status is ApprovalStatus.APPROVED
                    ),
                )
            )
        return days

    async def approve(self, suggestion_id: UUID, *, actor: str) -> DailySuggestion | None:
        """Approve a suggestion for the public board, stamping the actor and time.

        Allowed even after the reveal time: a late approval is additive, joining the
        day's board the next time it is read.

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

    async def delete(self, suggestion_id: UUID, *, actor: str) -> bool:
        """Permanently remove a suggestion, freeing its slot. False when none has that id.

        The hard counterpart to ``reject``: reject keeps the slot filled but off the
        board, delete empties it so the slot can be composed afresh. The actor is logged
        because a hard delete leaves nothing on the row to audit afterwards.
        """
        suggestion = await self._session.get(DailySuggestion, suggestion_id)
        if suggestion is None:
            return False
        await self._session.delete(suggestion)
        log.info("daily.deleted", suggestion_id=str(suggestion_id), actor=actor)
        return True

    def reveal_at_for(self, target: date, *, now: datetime) -> datetime:
        """The instant the target date's board unlocks.

        A fixed UTC hour on the target date, so the board premieres at the same moment
        worldwide. A same-day board is the exception: its reveal is clamped to now, so a
        board generated for today (a dev or fork install, or a late manual run) reveals
        the moment it is approved instead of waiting for an hour that may already have
        passed. Future dates keep the hour, preserving the simultaneous premiere.
        """
        reveal = datetime.combine(target, time(hour=settings.daily_reveal_hour_utc), tzinfo=UTC)
        return min(reveal, now) if target == now.date() else reveal

    async def slot_for(self, target: date, meal_type: MealType) -> DailySuggestion | None:
        """Return the suggestion in one (date, meal_type) slot, or None when empty."""
        stmt = select(DailySuggestion).where(
            DailySuggestion.suggestion_date == target,
            DailySuggestion.meal_type == meal_type,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get(self, suggestion_id: UUID) -> DailySuggestion | None:
        """Return one suggestion by id, or None when there is no match."""
        return await self._session.get(DailySuggestion, suggestion_id)

    def apply_edit(
        self, suggestion: DailySuggestion, payload: AdminDailyUpdate, *, unverified: list[str]
    ) -> None:
        """Rewrite a suggestion's content blob from a verified edit (no embedding).

        Daily rows are read by date, not by similarity, so there is no vector to keep in
        step; only the JSONB content changes. ``unverified`` is the re-derived not-indexed
        list. The caller commits.
        """
        content = DailyMealContent(
            name=payload.name,
            description=payload.description,
            ingredients=payload.ingredients,
            recipe=payload.recipe,
            tags=payload.tags,
            unverified_ingredients=unverified,
        )
        suggestion.content = content.model_dump()

    async def store_pending(
        self, meal: ComposedMeal, target: date, *, now: datetime
    ) -> DailySuggestion:
        """Upsert one composed meal into its (date, meal_type) slot as pending review.

        The pure per-slot write: find-or-create the slot, write the composed content,
        model, usage and trace, stamp the reveal time, and (re)set it to pending with
        any prior approval cleared. The skip and replace policy is the caller's, not
        enforced here, so a slot already holding a row is overwritten in place.
        """
        row = await self.slot_for(target, meal.meal_type)
        is_new = row is None
        if row is None:
            row = DailySuggestion()
        content = DailyMealContent(
            name=meal.name,
            description=meal.description,
            ingredients=meal.ingredients,
            recipe=meal.recipe,
            tags=meal.tags,
            unverified_ingredients=meal.unverified_ingredients,
        )
        row.suggestion_date = target
        row.meal_type = meal.meal_type
        row.content = content.model_dump()
        row.model = meal.model
        row.usage = meal.usage.model_dump()
        row.reasoning_trace = [event.model_dump() for event in meal.reasoning_trace]
        row.reveal_at = self.reveal_at_for(target, now=now)
        row.approval_status = ApprovalStatus.PENDING
        row.approved_at = None
        row.approved_by = None
        if is_new:
            self._session.add(row)
        return row

    async def prune_before(self, cutoff: date) -> int:
        """Delete suggestions dated before ``cutoff``, returning how many were removed.

        Run by the nightly cron to bound the table to the history window the public
        past-board view can still read. The caller commits.
        """
        deleted = await self._session.execute(
            delete(DailySuggestion)
            .where(DailySuggestion.suggestion_date < cutoff)
            .returning(DailySuggestion.id)
        )
        return len(deleted.all())

    async def _for_date(self, on: date) -> list[DailySuggestion]:
        stmt = select(DailySuggestion).where(DailySuggestion.suggestion_date == on)
        return list((await self._session.execute(stmt)).scalars().all())


def _to_card(row: DailySuggestion) -> DailyMealCard:
    content = DailyMealContent.model_validate(row.content)
    events = [TraceEvent.model_validate(raw) for raw in row.reasoning_trace]
    # unverified_ingredients is review-queue context, not a public card field; the trace
    # is filtered to the code-authored steps the public board may show. The model rides on
    # the card, not the board: an operator can regenerate one slot with a different model.
    return DailyMealCard(
        meal_type=row.meal_type,
        model=row.model,
        trace=public_trace(events),
        **content.model_dump(exclude={"unverified_ingredients"}),
    )


def _total_usage(rows: list[DailySuggestion]) -> LLMUsage:
    """Token usage of composing the day's board, summed across its meals.

    Rows composed before usage was recorded carry none and simply add nothing.
    """
    usages = [LLMUsage.model_validate(row.usage) for row in rows if row.usage]
    return LLMUsage(
        calls=sum(usage.calls for usage in usages),
        input_tokens=sum(usage.input_tokens for usage in usages),
        output_tokens=sum(usage.output_tokens for usage in usages),
        total_tokens=sum(usage.total_tokens for usage in usages),
        steps=[step for usage in usages for step in usage.steps],
    )
