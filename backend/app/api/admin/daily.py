"""Admin daily board: review the queue, and watch the composer compose live.

Every route is gated by ``get_current_admin``. Approval is what lets a meal reach
the public board, so the queue returns each suggestion's full content and reasoning
trace for the admin to actually check before signing off. The ``generate`` route is
the live demo: it streams one composition's reasoning as Server-Sent Events.
"""

import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sse_starlette.sse import EventSourceResponse

from app.agents.composer import ComposerExhausted
from app.core.ratelimit import limiter, llm_rate_limit
from app.dependencies import get_composer_streamer, get_current_admin, get_daily_service
from app.enums import ApprovalStatus
from app.llm.errors import LLMError
from app.models import DailySuggestion
from app.models.admin_user import AdminUser
from app.schemas.admin import AdminDailyRead, DailyGenerateRequest
from app.services.composer_streamer import ComposerStreamer
from app.services.daily_service import DailyService

router = APIRouter(prefix="/admin/daily", tags=["admin"])

_GENERATION_FAILED = "The composer could not finish a safe meal. Try again."


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


@router.post("/generate")
@limiter.limit(llm_rate_limit)
async def generate_live(
    request: Request,
    payload: DailyGenerateRequest,
    _admin: AdminUser = Depends(get_current_admin),
    streamer: ComposerStreamer = Depends(get_composer_streamer),
) -> EventSourceResponse:
    """Stream one live meal composition as Server-Sent Events.

    Each reasoning step is sent as a ``trace`` event and the finished meal as a
    terminal ``meal`` event; a failure becomes an ``error`` event so the already
    open stream closes cleanly rather than dropping. The meal is a live demo and is
    not saved: the nightly job populates the public board.
    """

    async def event_source() -> AsyncIterator[dict[str, str]]:
        try:
            async for chunk in streamer.stream(payload.meal_type):
                # The composer emits one JSON object per item; a trace step carries
                # a "kind", the terminal composed meal does not.
                parsed = json.loads(chunk)
                name = "trace" if isinstance(parsed, dict) and "kind" in parsed else "meal"
                yield {"event": name, "data": chunk}
        except ComposerExhausted:
            yield {"event": "error", "data": json.dumps({"detail": _GENERATION_FAILED})}
        except LLMError as exc:
            yield {"event": "error", "data": json.dumps({"detail": str(exc)})}

    return EventSourceResponse(event_source())
