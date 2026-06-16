"""Admin daily-board review: list the queue, approve, or reject a suggestion.

Every route is gated by ``get_current_admin``. Approval is what lets a meal reach
the public board, so the queue returns each suggestion's full content and reasoning
trace for the admin to actually check before signing off.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_current_admin, get_daily_service
from app.enums import ApprovalStatus
from app.models import DailySuggestion
from app.models.admin_user import AdminUser
from app.schemas.admin import AdminDailyRead
from app.services.daily_service import DailyService

router = APIRouter(prefix="/admin/daily", tags=["admin"])


@router.get("", response_model=list[AdminDailyRead])
async def list_daily(
    status: ApprovalStatus = Query(
        default=ApprovalStatus.PENDING, description="Which review state to list."
    ),
    _admin: AdminUser = Depends(get_current_admin),
    service: DailyService = Depends(get_daily_service),
) -> list[DailySuggestion]:
    """List suggestions in one review state, soonest reveal date first."""
    return await service.list_for_review(status)


@router.patch("/{suggestion_id}/approve", response_model=AdminDailyRead)
async def approve_suggestion(
    suggestion_id: UUID,
    admin: AdminUser = Depends(get_current_admin),
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
    _admin: AdminUser = Depends(get_current_admin),
    service: DailyService = Depends(get_daily_service),
) -> DailySuggestion:
    """Reject a suggestion, keeping it off the board."""
    suggestion = await service.reject(suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found.")
    return suggestion
