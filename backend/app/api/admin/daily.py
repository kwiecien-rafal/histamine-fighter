"""Admin daily board: review the queue, and approve or reject a suggestion.

Every route is gated by ``require_admin``. Approval is what lets a meal reach the
public board, so the review list returns each suggestion's full content and reasoning
trace for the admin to actually check before signing off. Live composition (and saving
into the queue) lives in the sibling ``compose`` router.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.admin.edits import ensure_safe, verify_edit
from app.dependencies import get_daily_service, get_ingredient_service, require_admin
from app.enums import ApprovalStatus
from app.models import DailySuggestion
from app.models.user import User
from app.schemas.admin import AdminDailyRead, AdminDailyUpdate, QueuedDay
from app.services.daily_service import DailyService
from app.services.ingredient_service import IngredientService

router = APIRouter(prefix="/admin/daily", tags=["admin"])

_NOT_PENDING = "Only a pending suggestion can be edited."


@router.get("/queue", response_model=list[QueuedDay])
async def daily_queue(
    _admin: User = Depends(require_admin),
    service: DailyService = Depends(get_daily_service),
) -> list[QueuedDay]:
    """Group the upcoming board (today onward) by date for the queue view."""
    return await service.list_queue(today=datetime.now(UTC).date())


@router.patch("/{suggestion_id}", response_model=AdminDailyRead)
async def update_suggestion(
    suggestion_id: UUID,
    payload: AdminDailyUpdate,
    _admin: User = Depends(require_admin),
    service: DailyService = Depends(get_daily_service),
    ingredient_service: IngredientService = Depends(get_ingredient_service),
) -> DailySuggestion:
    """Edit a pending daily suggestion, re-verifying it against the index before saving.

    Allowed only while pending (409 otherwise). The edit is re-run through the admin
    index gate, so an introduced flagged ingredient is a 422 the admin can confirm past
    with ``confirm_flagged``, and the not-indexed list is re-derived.
    """
    suggestion = await service.get(suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found.")
    if suggestion.approval_status is not ApprovalStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NOT_PENDING)
    verification = await verify_edit(ingredient_service, payload)
    confirmed_flags = ensure_safe(verification, confirmed=payload.confirm_flagged)
    service.apply_edit(suggestion, payload, unverified=verification.unverified + confirmed_flags)
    return suggestion


@router.patch("/{suggestion_id}/approve", response_model=AdminDailyRead)
async def approve_suggestion(
    suggestion_id: UUID,
    admin: User = Depends(require_admin),
    service: DailyService = Depends(get_daily_service),
) -> DailySuggestion:
    """Approve a suggestion for the public board, stamped with the approving admin."""
    suggestion = await service.approve(suggestion_id, actor=admin.email)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found.")
    return suggestion


@router.patch("/{suggestion_id}/reject", response_model=AdminDailyRead)
async def reject_suggestion(
    suggestion_id: UUID,
    _admin: User = Depends(require_admin),
    service: DailyService = Depends(get_daily_service),
) -> DailySuggestion:
    """Reject a suggestion, keeping it off the board."""
    suggestion = await service.reject(suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found.")
    return suggestion


@router.delete("/{suggestion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_suggestion(
    suggestion_id: UUID,
    admin: User = Depends(require_admin),
    service: DailyService = Depends(get_daily_service),
) -> None:
    """Permanently remove a suggestion, freeing its slot (the hard counterpart to reject)."""
    if not await service.delete(suggestion_id, actor=admin.email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found.")
