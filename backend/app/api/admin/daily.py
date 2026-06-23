"""Admin daily board: review the queue, and watch the composer compose live.

Every route is gated by ``require_admin``. Approval is what lets a meal reach the
public board, so the queue returns each suggestion's full content and reasoning
trace for the admin to actually check before signing off. The ``generate`` route is
the live demo: it streams one composition's reasoning as Server-Sent Events.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sse_starlette.sse import EventSourceResponse

from app.agents.composer import ComposerExhausted
from app.core.ratelimit import limiter, llm_rate_limit
from app.dependencies import get_composer_streamer, get_daily_service, require_admin
from app.enums import ApprovalStatus
from app.llm.errors import LLMError
from app.models import DailySuggestion
from app.models.user import User
from app.schemas.admin import AdminDailyRead, DailyGenerateRequest
from app.services.composer_streamer import ComposerStreamer
from app.services.daily_service import DailyService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/daily", tags=["admin"])

_GENERATION_FAILED = "The composer could not finish a safe meal. Try again."
_GENERATION_ERROR = "Something went wrong while composing the meal. Try again."
_GENERATION_BUSY = "A live composition is already running. Wait for it to finish."

# Serializes the live composer so one admin trigger cannot fan out into several
# concurrent multi-call LLM runs (the per-IP rate limit bounds rate, not overlap).
# Process-local: under multiple workers it guards per worker, which is enough for a
# single-operator demo; a cross-process guard would be over-engineered here.
_generation_lock = asyncio.Lock()


@router.get("", response_model=list[AdminDailyRead])
async def list_daily(
    status: ApprovalStatus = Query(
        default=ApprovalStatus.PENDING, description="Which review state to list."
    ),
    _admin: User = Depends(require_admin),
    service: DailyService = Depends(get_daily_service),
) -> list[DailySuggestion]:
    """List suggestions in one review state, soonest reveal date first."""
    return await service.list_for_review(status)


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


def _log_done(meal_type: str, meal: dict[str, Any]) -> None:
    """Record a finished live composition: the model, the meal, and the token cost.

    ``compose`` logs the offline batch path; the live stream went unlogged, so an
    admin trigger spent tokens with no server-side record of what it cost or made.
    """
    usage = meal.get("usage") or {}
    log.info(
        "composer.live.done",
        meal_type=meal_type,
        model=meal.get("model"),
        name=meal.get("name"),
        ingredients=len(meal.get("ingredients") or []),
        unverified=len(meal.get("unverified_ingredients") or []),
        calls=usage.get("calls"),
        total_tokens=usage.get("total_tokens"),
    )


@router.post("/generate")
@limiter.limit(llm_rate_limit)
async def generate_live(
    request: Request,
    payload: DailyGenerateRequest,
    _admin: User = Depends(require_admin),
    streamer: ComposerStreamer = Depends(get_composer_streamer),
) -> EventSourceResponse:
    """Stream one live meal composition as Server-Sent Events.

    Each reasoning step is sent as a ``trace`` event and the finished meal as a
    terminal ``meal`` event; a failure becomes an ``error`` event so the already
    open stream closes cleanly rather than dropping. The composer is expensive, so
    only one live run is allowed at a time: a second trigger gets 409 while one is
    in flight. The meal is a live demo and is not saved: the nightly job populates
    the public board.
    """
    if _generation_lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_GENERATION_BUSY)

    async def event_source() -> AsyncIterator[dict[str, str]]:
        # The lock is the real guard: even if two requests slip past the check above,
        # they run one at a time, and it releases here on completion or client cancel.
        async with _generation_lock:
            try:
                async for chunk in streamer.stream(payload.meal_type):
                    # Each item is a discriminated envelope: {"type": "trace", "event": ...}
                    # or {"type": "meal", "meal": ...}. The SSE event name is its type and
                    # the inner object is the payload the client already expects.
                    envelope = json.loads(chunk)
                    event_type = envelope["type"]
                    inner = envelope["event"] if event_type == "trace" else envelope["meal"]
                    yield {"event": event_type, "data": json.dumps(inner)}
                    if event_type == "meal":
                        _log_done(payload.meal_type.value, inner)
            except ComposerExhausted:
                log.warning("composer.live.exhausted", meal_type=payload.meal_type.value)
                yield {"event": "error", "data": json.dumps({"detail": _GENERATION_FAILED})}
            except LLMError as exc:
                log.warning(
                    "composer.live.llm_error",
                    meal_type=payload.meal_type.value,
                    error=str(exc),
                )
                yield {"event": "error", "data": json.dumps({"detail": str(exc)})}
            except Exception:
                # The 200 and headers are already sent, so an unexpected failure cannot
                # become an HTTP error: it has to close the stream as an error event, or
                # the client reads a truncated stream as success. A client disconnect
                # raises CancelledError (a BaseException), so it still propagates here.
                log.exception("composer.live.failed", meal_type=payload.meal_type.value)
                yield {"event": "error", "data": json.dumps({"detail": _GENERATION_ERROR})}

    return EventSourceResponse(event_source())
