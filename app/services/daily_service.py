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
from app.schemas.meal import CautionedIngredient, ComposedMeal, TraceEvent, public_trace
from app.schemas.usage import LLMUsage
from app.services.ingredient_service import IngredientService
from app.services.meal_edit import (
    EditTargetMissing,
    EditTargetNotPending,
    ensure_safe,
    verify_edit,
)

log = structlog.get_logger(__name__)

# Cards and the replayed trace follow the natural meal order, not the alphabetical
# order a SQL sort on the enum value would give.
_MEAL_ORDER = {meal_type: index for index, meal_type in enumerate(MealType)}


class DailyService:
    """Reads, generates, and moderates the daily board. Never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def board_for(self, on: date, *, now: datetime) -> LockedBoard | RevealedBoard:
        """Return the board for a date, locked or revealed."""
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
        """Group the upcoming suggestions (today onward) by date for the admin queue."""
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
        """Approve a suggestion for the public board, stamping the actor and time."""
        suggestion = await self._session.get(DailySuggestion, suggestion_id)
        if suggestion is None:
            return None
        suggestion.approval_status = ApprovalStatus.APPROVED
        suggestion.approved_at = datetime.now(UTC)
        suggestion.approved_by = actor
        log.info("daily.approved", suggestion_id=str(suggestion_id), actor=actor)
        return suggestion

    async def reject(self, suggestion_id: UUID) -> DailySuggestion | None:
        """Reject a suggestion, clearing any prior approval stamp."""
        suggestion = await self._session.get(DailySuggestion, suggestion_id)
        if suggestion is None:
            return None
        suggestion.approval_status = ApprovalStatus.REJECTED
        suggestion.approved_at = None
        suggestion.approved_by = None
        log.info("daily.rejected", suggestion_id=str(suggestion_id))
        return suggestion

    async def delete(self, suggestion_id: UUID, *, actor: str) -> bool:
        """Permanently remove a suggestion, freeing its slot."""
        suggestion = await self._session.get(DailySuggestion, suggestion_id)
        if suggestion is None:
            return False
        await self._session.delete(suggestion)
        log.info("daily.deleted", suggestion_id=str(suggestion_id), actor=actor)
        return True

    def earliest_readable_date(self, today: date) -> date:
        """The oldest board date still readable, and the date the pruner retains from."""
        return today - timedelta(days=settings.daily_history_days)

    def reveal_at_for(self, target: date, *, now: datetime) -> datetime:
        """The instant the target date's board unlocks."""
        reveal = datetime.combine(target, time(hour=settings.daily_reveal_hour_utc), tzinfo=UTC)
        return min(reveal, now) if target == now.date() else reveal

    async def open_meal_types(self, target: date) -> list[MealType]:
        """The slots of a date a board run may fill: empty or rejected, in meal order."""
        rows = await self._for_date(target)
        blocked = {
            row.meal_type for row in rows if row.approval_status is not ApprovalStatus.REJECTED
        }
        return [meal_type for meal_type in MealType if meal_type not in blocked]

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

    async def edit_pending(
        self,
        suggestion_id: UUID,
        payload: AdminDailyUpdate,
        *,
        ingredients: IngredientService,
    ) -> DailySuggestion:
        """Rewrite a pending daily slot, re-verified against the index before saving."""
        suggestion = await self.get(suggestion_id)
        if suggestion is None:
            raise EditTargetMissing("Suggestion not found.")
        if suggestion.approval_status is not ApprovalStatus.PENDING:
            raise EditTargetNotPending("Only a pending suggestion can be edited.")
        verification = await verify_edit(ingredients, payload)
        confirmed_flags = ensure_safe(verification, confirmed=payload.confirm_flagged)
        self.apply_edit(
            suggestion,
            payload,
            unverified=verification.unverified + confirmed_flags,
            cautioned=verification.cautioned,
        )
        return suggestion

    def apply_edit(
        self,
        suggestion: DailySuggestion,
        payload: AdminDailyUpdate,
        *,
        unverified: list[str],
        cautioned: list[CautionedIngredient],
    ) -> None:
        """Rewrite a suggestion's content blob from a verified edit (no embedding)."""
        content = DailyMealContent(
            name=payload.name,
            description=payload.description,
            ingredients=payload.ingredients,
            recipe=payload.recipe,
            tags=payload.tags,
            unverified_ingredients=unverified,
            cautioned_ingredients=cautioned,
        )
        suggestion.content = content.model_dump()

    async def store_pending(
        self, meal: ComposedMeal, target: date, *, now: datetime
    ) -> DailySuggestion:
        """Upsert one composed meal into its (date, meal_type) slot as pending review."""
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
            cautioned_ingredients=meal.cautioned_ingredients,
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

    async def recent_meal_names(self, *, before: date, days: int) -> list[str]:
        """Dish names on boards up to and including ``before``, newest first."""
        if days <= 0:
            return []
        stmt = (
            select(DailySuggestion.content)
            .where(
                DailySuggestion.suggestion_date >= before - timedelta(days=days),
                DailySuggestion.suggestion_date <= before,
            )
            .order_by(DailySuggestion.suggestion_date.desc())
        )
        names: list[str] = []
        seen: set[str] = set()
        for content in (await self._session.scalars(stmt)).all():
            name = DailyMealContent.model_validate(content).name
            if name.casefold() not in seen:
                seen.add(name.casefold())
                names.append(name)
        return names

    async def prune_before(self, cutoff: date) -> int:
        """Delete suggestions dated before ``cutoff``, returning how many were removed."""
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
    # unverified_ingredients is review-queue context, not a public card field, while
    # cautioned_ingredients rides through: which ingredients to moderate is visitor
    # guidance. The trace is filtered to the code-authored steps the public board may
    # show. The model rides on the card, not the board: an operator can regenerate one
    # slot with a different model.
    return DailyMealCard(
        id=row.id,
        meal_type=row.meal_type,
        model=row.model,
        trace=public_trace(events),
        **content.model_dump(exclude={"unverified_ingredients"}),
    )


def _total_usage(rows: list[DailySuggestion]) -> LLMUsage:
    """Token usage of composing the day's board, summed across its meals."""
    usages = [LLMUsage.model_validate(row.usage) for row in rows if row.usage]
    return LLMUsage(
        calls=sum(usage.calls for usage in usages),
        input_tokens=sum(usage.input_tokens for usage in usages),
        output_tokens=sum(usage.output_tokens for usage in usages),
        total_tokens=sum(usage.total_tokens for usage in usages),
        steps=[step for usage in usages for step in usage.steps],
    )
