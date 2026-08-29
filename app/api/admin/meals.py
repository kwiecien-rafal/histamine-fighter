"""Admin meal review: list the queue, approve, or reject a composed meal.

Every route is gated by ``require_admin``. Approval is what lets the public board
claim a meal is verified, so the queue returns each meal's full ingredient list and
reasoning trace for the admin to actually check before signing off.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import (
    get_ingredient_service,
    get_meal_review_service,
    get_meal_service,
    require_admin,
)
from app.enums import ApprovalStatus
from app.models import CuratedMeal
from app.models.user import User
from app.schemas.admin import AdminMealCreate, AdminMealRead, AdminMealUpdate
from app.services.ingredient_service import IngredientService
from app.services.meal_edit import EditTargetMissing, EditTargetNotPending, UnsafeMealEdit
from app.services.meal_review_service import MealReviewService
from app.services.meal_service import MealService

router = APIRouter(prefix="/admin/meals", tags=["admin"])


def _refused(exc: UnsafeMealEdit) -> HTTPException:
    """The index gate's refusal, with the flagged items the form has to show."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "message": exc.message,
            "blockers": exc.blockers,
            "can_confirm": exc.can_confirm,
        },
    )


@router.get("", response_model=list[AdminMealRead])
async def list_meals(
    # alias keeps the query key ?status=... while the local name avoids shadowing
    # the imported fastapi.status used by the sibling handlers.
    approval_status: ApprovalStatus = Query(
        default=ApprovalStatus.PENDING, alias="status", description="Which review state to list."
    ),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum meals to return."),
    offset: int = Query(default=0, ge=0, description="How many meals to skip."),
    _admin: User = Depends(require_admin),
    service: MealReviewService = Depends(get_meal_review_service),
) -> list[CuratedMeal]:
    """List one page of meals in a review state, oldest first (defaults to pending)."""
    return await service.list_by_status(approval_status, limit=limit, offset=offset)


@router.post("", response_model=AdminMealRead, status_code=status.HTTP_201_CREATED)
async def create_meal(
    payload: AdminMealCreate,
    admin: User = Depends(require_admin),
    meal_service: MealService = Depends(get_meal_service),
    ingredient_service: IngredientService = Depends(get_ingredient_service),
) -> CuratedMeal:
    """Author a manual (non-LLM) meal, vetted by the admin index gate.

    A hand-written meal runs the same ingredient re-check an edit does: a flagged
    ingredient is a 422 the admin can confirm past with ``confirm_flagged`` (recorded
    for the reviewer), an unverifiable one always blocks. It lands pending, marked with
    the ``manual`` model sentinel, for the same admin approval a composed meal needs.
    """
    try:
        return await meal_service.create_manual(
            payload, actor=admin.email, ingredients=ingredient_service
        )
    except UnsafeMealEdit as exc:
        raise _refused(exc) from exc


@router.patch("/{meal_id}", response_model=AdminMealRead)
async def update_meal(
    meal_id: UUID,
    payload: AdminMealUpdate,
    _admin: User = Depends(require_admin),
    meal_service: MealService = Depends(get_meal_service),
    ingredient_service: IngredientService = Depends(get_ingredient_service),
) -> CuratedMeal:
    """Edit a pending curated meal, re-verifying it against the index before saving.

    Allowed only while pending (409 otherwise). The edited list is re-run through the
    admin index gate, so an introduced flagged ingredient is a 422 the admin can confirm
    past with ``confirm_flagged``, and the not-indexed list is re-derived.
    """
    try:
        return await meal_service.edit_pending(meal_id, payload, ingredients=ingredient_service)
    except EditTargetMissing as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EditTargetNotPending as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnsafeMealEdit as exc:
        raise _refused(exc) from exc


@router.patch("/{meal_id}/approve", response_model=AdminMealRead)
async def approve_meal(
    meal_id: UUID,
    admin: User = Depends(require_admin),
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
    _admin: User = Depends(require_admin),
    service: MealReviewService = Depends(get_meal_review_service),
) -> CuratedMeal:
    """Reject a meal, keeping it out of the pool."""
    meal = await service.reject(meal_id)
    if meal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal not found.")
    return meal


@router.delete("/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meal(
    meal_id: UUID,
    admin: User = Depends(require_admin),
    service: MealReviewService = Depends(get_meal_review_service),
) -> None:
    """Permanently remove a meal from the pool (the hard counterpart to reject)."""
    if not await service.delete(meal_id, actor=admin.email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal not found.")
