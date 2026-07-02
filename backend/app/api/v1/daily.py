"""Public daily board: pre-composed, admin-approved meals for today or a past day.

A plain database read, no LLM call: the expensive composition ran offline overnight,
and the reveal is a clock check against ``reveal_at``. The board is locked until it
unlocks and is approved, then revealed with the meals, each carrying its recorded
trace for an on-demand "how it was composed" replay. The dated route serves the same
board for any day within the history window, so a visitor can step back through past
boards; a past day that never reached an approval reads as locked ("no board").

The response carries a ``Cache-Control``. A past day is immutable, so it caches long;
today's locked board caches until it unlocks (so the cache expires as it reveals) and
its revealed board for a couple of minutes. This, not a rate limit, is what keeps the
reload every client fires at the reveal instant from stampeding the read.
"""

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response

from app.config import settings
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
# A past day cannot change in any way a visitor would wait on (a late approval is the
# rare exception), so it caches for an hour rather than tracking the reveal clock.
_PAST_BOARD_MAX_AGE = 3600


def _cache_max_age(board: LockedBoard | RevealedBoard, now: datetime) -> int:
    """Seconds the board read may be cached, from its state and time to reveal."""
    if board.date < now.date():
        return _PAST_BOARD_MAX_AGE
    if isinstance(board, RevealedBoard):
        return _REVEALED_MAX_AGE
    if board.reveal_at is None:
        return _LOCKED_MAX_AGE_FLOOR
    until_reveal = int((board.reveal_at - now).total_seconds())
    return max(_LOCKED_MAX_AGE_FLOOR, min(until_reveal, _LOCKED_MAX_AGE_CAP))


async def _board_response(
    on: date, response: Response, service: DailyService
) -> LockedBoard | RevealedBoard:
    """Read the board for a date and stamp its cache header. Shared by both routes."""
    now = datetime.now(UTC)
    board = await service.board_for(on, now=now)
    response.headers["Cache-Control"] = f"public, max-age={_cache_max_age(board, now)}"
    return board


@router.get("/meals", response_model=DailyBoard)
async def daily_meals(
    response: Response,
    service: DailyService = Depends(get_daily_service),
) -> LockedBoard | RevealedBoard:
    """Return today's board, locked before its reveal or revealed after it.

    "Today" and the reveal comparison are both in UTC, so the board unlocks at the
    same instant for every visitor regardless of their timezone.
    """
    return await _board_response(datetime.now(UTC).date(), response, service)


@router.get("/meals/{on}", response_model=DailyBoard)
async def daily_meals_on(
    on: date,
    response: Response,
    service: DailyService = Depends(get_daily_service),
) -> LockedBoard | RevealedBoard:
    """Return the board for a past day within the history window.

    Accepts ``today - daily_history_days`` through today (today included, so the route
    is robust if a client uses it for the current day too); any date outside that range
    is a 404, since a future board is unpublished and an older one has been pruned.
    """
    today = datetime.now(UTC).date()
    if not today - timedelta(days=settings.daily_history_days) <= on <= today:
        raise HTTPException(status_code=404, detail="No board is available for that date.")
    return await _board_response(on, response, service)
