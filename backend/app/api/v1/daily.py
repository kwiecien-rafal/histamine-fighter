"""Public daily board: today's pre-composed, admin-approved meals.

A plain database read, no LLM call and no rate limit: the expensive composition
ran offline overnight, and the reveal is a clock check against ``reveal_at``. The
board is locked until it unlocks and is approved, then revealed with the meals and
the composer's recorded trace for the premiere replay.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.dependencies import get_daily_service
from app.schemas.daily import DailyBoard, LockedBoard, RevealedBoard
from app.services.daily_service import DailyService

router = APIRouter(prefix="/api/v1/daily", tags=["daily"])


@router.get("/meals", response_model=DailyBoard)
async def daily_meals(
    service: DailyService = Depends(get_daily_service),
) -> LockedBoard | RevealedBoard:
    """Return today's board, locked before its reveal or revealed after it.

    "Today" and the reveal comparison are both in UTC, so the board unlocks at the
    same instant for every visitor regardless of their timezone.
    """
    now = datetime.now(UTC)
    return await service.board_for(now.date(), now=now)
