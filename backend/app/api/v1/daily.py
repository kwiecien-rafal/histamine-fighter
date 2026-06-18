"""Public daily board: today's pre-composed, admin-approved meals.

A plain database read, no LLM call: the expensive composition ran offline overnight,
and the reveal is a clock check against ``reveal_at``. The board is locked until it
unlocks and is approved, then revealed with the meals and the composer's recorded
trace for the premiere replay.

The response carries a short ``Cache-Control``. A locked board is cacheable until it
unlocks (so the cache expires as it reveals); a revealed board for a couple of
minutes. This, not a rate limit, is what keeps the reload every client fires at the
reveal instant from stampeding the read.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response

from app.dependencies import get_daily_service
from app.schemas.daily import DailyBoard, LockedBoard, RevealedBoard
from app.services.daily_service import DailyService

router = APIRouter(prefix="/api/v1/daily", tags=["daily"])

# A revealed board is essentially fixed for the day; a short cache still lets a late
# approval surface within a couple of minutes.
_REVEALED_MAX_AGE = 120
# A locked board is cached until its reveal, but never past this cap (a board fetched
# hours early) nor below this floor — which also covers the brief "past reveal, not
# yet approved" and "no board scheduled" windows, where clients should recheck soon.
_LOCKED_MAX_AGE_CAP = 300
_LOCKED_MAX_AGE_FLOOR = 30


def _cache_max_age(board: LockedBoard | RevealedBoard, now: datetime) -> int:
    """Seconds the board read may be cached, from its state and time to reveal."""
    if isinstance(board, RevealedBoard):
        return _REVEALED_MAX_AGE
    if board.reveal_at is None:
        return _LOCKED_MAX_AGE_FLOOR
    until_reveal = int((board.reveal_at - now).total_seconds())
    return max(_LOCKED_MAX_AGE_FLOOR, min(until_reveal, _LOCKED_MAX_AGE_CAP))


@router.get("/meals", response_model=DailyBoard)
async def daily_meals(
    response: Response,
    service: DailyService = Depends(get_daily_service),
) -> LockedBoard | RevealedBoard:
    """Return today's board, locked before its reveal or revealed after it.

    "Today" and the reveal comparison are both in UTC, so the board unlocks at the
    same instant for every visitor regardless of their timezone.
    """
    now = datetime.now(UTC)
    board = await service.board_for(now.date(), now=now)
    response.headers["Cache-Control"] = f"public, max-age={_cache_max_age(board, now)}"
    return board
