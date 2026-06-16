"""Admin meal review: list the queue, approve, or reject a composed meal.

Every route is gated by ``get_current_admin``. Approval is what lets the public
board claim a meal is verified, so the queue returns each meal's full ingredient
list and reasoning trace for the admin to actually check before signing off.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_current_admin, get_meal_review_service
from app.enums import ApprovalStatus
from app.models import CuratedMeal
from app.models.admin_user import AdminUser
from app.schemas.admin import AdminMealRead
from app.services.meal_review_service import MealReviewService

router = APIRouter(prefix="/admin/meals", tags=["admin"])


@router.get("", response_model=list[AdminMealRead])
async def list_meals(
    status: ApprovalStatus = Query(
        default=ApprovalStatus.PENDING, description="Which review state to list."
    ),
    _admin: AdminUser = Depends(get_current_admin),
    service: MealReviewService = Depends(get_meal_review_service),
) -> list[CuratedMeal]:
    """List meals in one review state, newest first (defaults to the pending queue)."""
    return await service.list_by_status(status)


@router.patch("/{meal_id}/approve", response_model=AdminMealRead)
async def approve_meal(
    meal_id: UUID,
    admin: AdminUser = Depends(get_current_admin),
    service: MealReviewService = Depends(get_meal_review_service),
) -> CuratedMeal:
    """Approve a meal for the public pool, stamped with the approving admin."""
    meal = await service.approve(meal_id, actor=admin.email)
    if meal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal not found.")
    return meal


@router.patch("/{meal_id}/reject", response_model=AdminMealRead)
async def reject_meal(
    meal_id: UUID,
    _admin: AdminUser = Depends(get_current_admin),
    service: MealReviewService = Depends(get_meal_review_service),
) -> CuratedMeal:
    """Reject a meal, keeping it out of the pool."""
    meal = await service.reject(meal_id)
    if meal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal not found.")
    return meal
