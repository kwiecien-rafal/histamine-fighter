"""The public meal pages: the daily board, the curated browse, and one meal in full.

A signed-in visitor can save a meal straight from the board or a meal's page, so
these reads also ask which of the meals on screen are already on that visitor's
shelf — one query per page, not one per card.
"""

from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse

from app.dependencies import (
    get_current_user_optional,
    get_daily_service,
    get_meal_service,
    get_saved_meal_service,
)
from app.enums import MealType, SaveSource
from app.models.user import User
from app.services.daily_service import DailyService
from app.services.meal_service import MealService, public_card, public_detail
from app.services.saved_meal_service import SavedMealService
from app.web.deps import current_path, templates

router = APIRouter()

# One screen of browse cards; the pager walks the approved pool in these steps.
BROWSE_PAGE_SIZE = 24


@router.get("/daily", response_class=HTMLResponse)
async def daily_board(
    request: Request,
    on: date | None = Query(default=None, description="A past day within the history window."),
    service: DailyService = Depends(get_daily_service),
    saves: SavedMealService = Depends(get_saved_meal_service),
    user: User | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """Today's board, or a past day's, with links to step through the history window.

    The UTC clock and the window match the JSON route's, so the days a visitor can
    reach here are exactly the days the API will serve.
    """
    now = datetime.now(UTC)
    today = now.date()
    earliest = service.earliest_readable_date(today)
    viewing = on or today
    if not earliest <= viewing <= today:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No board is available for that date."
        )

    board = await service.board_for(viewing, now=now)
    saved = (
        await saves.saves_for_sources(
            user.id, SaveSource.DAILY, [str(meal.id) for meal in board.meals]
        )
        if user is not None and board.status == "revealed"
        else {}
    )
    return templates.TemplateResponse(
        request,
        "daily.html",
        {
            "board": board,
            "is_today": viewing == today,
            "saved": saved,
            "back_url": current_path(request),
            "previous_url": _board_url(viewing - timedelta(days=1)) if viewing > earliest else None,
            "next_url": _board_url(viewing + timedelta(days=1)) if viewing < today else None,
        },
    )


@router.get("/meals", response_class=HTMLResponse)
async def browse_meals(
    request: Request,
    meal_type: MealType | None = Query(default=None, description="Filter to one meal type."),
    offset: int = Query(default=0, ge=0, description="How many meals to skip."),
    service: MealService = Depends(get_meal_service),
) -> HTMLResponse:
    """One page of the approved pool, filterable by meal type and paged by links.

    Paging is a plain link with an ``offset``, so the browser's back button and a
    shared URL both land on the page the visitor was actually looking at.
    """
    rows, total = await service.list_approved(
        meal_type=meal_type, limit=BROWSE_PAGE_SIZE, offset=offset
    )
    next_offset = offset + BROWSE_PAGE_SIZE
    return templates.TemplateResponse(
        request,
        "meals.html",
        {
            "meals": [public_card(row) for row in rows],
            "total": total,
            "meal_type": meal_type,
            "meal_types": list(MealType),
            "browse_url": _browse_url,
            "first_shown": offset + 1,
            "last_shown": offset + len(rows),
            "previous_url": _browse_url(meal_type, max(offset - BROWSE_PAGE_SIZE, 0))
            if offset
            else None,
            "next_url": _browse_url(meal_type, next_offset) if next_offset < total else None,
        },
    )


@router.get("/meals/{meal_id}", response_class=HTMLResponse)
async def meal_detail(
    request: Request,
    meal_id: UUID,
    service: MealService = Depends(get_meal_service),
    saves: SavedMealService = Depends(get_saved_meal_service),
    user: User | None = Depends(get_current_user_optional),
) -> HTMLResponse:
    """One approved meal in full. A pending, rejected, or unknown id all read as missing."""
    row = await service.get_approved(meal_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That meal is not in the public pool."
        )
    saved = (
        await saves.saves_for_sources(user.id, SaveSource.CURATED, [str(meal_id)])
        if user is not None
        else {}
    )
    return templates.TemplateResponse(
        request,
        "meal_detail.html",
        {
            "meal": public_detail(row),
            "save_id": saved.get(str(meal_id)),
            "back_url": current_path(request),
        },
    )


def _board_url(on: date) -> str:
    """A daily-board link for one day."""
    return f"/daily?on={on}"


def _browse_url(meal_type: MealType | None, offset: int) -> str:
    """A browse link for one filter and page.

    Passed into the template as well as used for the pager, so every browse URL in the
    page — filter links included — is spelled in exactly one place.
    """
    params: list[tuple[str, str]] = []
    if meal_type is not None:
        params.append(("meal_type", meal_type.value))
    if offset:
        params.append(("offset", str(offset)))
    return f"/meals?{urlencode(params)}" if params else "/meals"
