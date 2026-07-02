"""Admin compose: stream a live composition into a pending row, and set the model.

Every route is gated by ``require_admin``. The streaming routes share one
process-local lock so an operator's repeated triggers cannot overlap into several
concurrent multi-call LLM runs within a worker (per-worker, see ``_compose_lock``).
Each persists the finished, trace-carrying meal as pending review and confirms it with
a ``saved`` frame: curated into the pool, daily into a dated slot, and the board route
fills every open slot of a date in one stream. The settings routes
read and set the operator's composer provider/model, validated through the single
source of provider truth so a keyless or gated choice can never be saved.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agents.composer import ComposerExhausted
from app.config import settings
from app.core.ratelimit import limiter, llm_rate_limit
from app.dependencies import (
    get_composer_streamer,
    get_daily_service,
    get_generation_settings_service,
    require_admin,
)
from app.embeddings import Embedder, get_embedder
from app.enums import ApprovalStatus, MealType
from app.llm.config import LLMRequestConfig
from app.llm.errors import LLMError
from app.llm.providers import resolve_llm_config, selectable_providers
from app.models import DailySuggestion
from app.models.user import User
from app.schemas.admin import (
    ComposeBoardRequest,
    ComposeDailyRequest,
    ComposeRequest,
    ComposeSettingsRead,
    ComposeSettingsUpdate,
)
from app.schemas.meal import BoardSummaryEvent, ComposedMeal, SlotStartEvent
from app.services.composer_streamer import ComposerStreamer, Persist
from app.services.daily_service import DailyService
from app.services.generation_settings_service import GenerationSettingsService
from app.services.meal_service import MealService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/compose", tags=["admin"])

_GENERATION_FAILED = "The composer could not finish a safe meal. Try again."
_GENERATION_ERROR = "Something went wrong while composing the meal. Try again."
_GENERATION_BUSY = "A live composition is already running. Wait for it to finish."
_SLOT_TAKEN = "That daily slot already holds a suggestion. Confirm to replace it."
_BOARD_FULL = "Every slot on that date is already filled. Reject or remove one first."

# Serializes the live composer so one admin trigger cannot fan out into several
# concurrent multi-call LLM runs (the per-IP rate limit bounds rate, not overlap).
# Process-local: under multiple workers it guards per worker, which is enough for a
# single-operator panel; a cross-process guard would be over-engineered here.
_compose_lock = asyncio.Lock()


def _log_done(meal_type: str, meal: dict[str, Any]) -> None:
    """Record a finished live composition: the model, the meal, and the token cost."""
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


def _ensure_within_queue_window(target: date, now: datetime) -> None:
    """Reject a daily date outside the manual-queue window before any work begins."""
    today = now.date()
    latest = today + timedelta(days=settings.daily_queue_max_ahead_days)
    if not today <= target <= latest:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"date must be between {today.isoformat()} and {latest.isoformat()}.",
        )


def _conflict_detail(
    existing: DailySuggestion | None, payload: ComposeDailyRequest
) -> dict[str, object] | None:
    """The 409 body for a taken daily slot, or None when the save may proceed.

    A non-rejected slot blocks unless the request opted into a replace. The body reports
    the slot's status so the UI can word the confirm by stakes (replacing an approved row
    un-publishes it). Shared by the route pre-check and the persist re-check so the rule
    is stated once.
    """
    if existing is None or existing.approval_status is ApprovalStatus.REJECTED or payload.replace:
        return None
    return {
        "message": _SLOT_TAKEN,
        "conflict": {
            "date": payload.date.isoformat(),
            "meal_type": payload.meal_type.value,
            "existing_status": existing.approval_status.value,
        },
    }


def _failure_frame(event: str, detail: str, meal_type: MealType) -> dict[str, str]:
    return {"event": event, "data": json.dumps({"detail": detail, "meal_type": meal_type.value})}


async def _slot_frames(
    meal_type: MealType, streamer: ComposerStreamer, persist: Persist, *, error_event: str
) -> AsyncIterator[dict[str, str]]:
    """Relay one composition's frames, closing its failures as ``error_event``.

    Compose failures (the loop's budget, a model error) and the streamer's own save
    failure all land on ``error_event``: the single-slot stream passes ``error`` so
    the failure is terminal, the board passes ``slot_error`` so it can move on to the
    remaining slots. An unexpected exception propagates to the caller's backstop.
    """
    try:
        async for frame in streamer.stream(meal_type, persist=persist):
            if frame["event"] == "error":
                frame = {"event": error_event, "data": frame["data"]}
            yield frame
            if frame["event"] == "meal":
                _log_done(meal_type.value, json.loads(frame["data"]))
    except ComposerExhausted:
        log.warning("composer.live.exhausted", meal_type=meal_type.value)
        yield _failure_frame(error_event, _GENERATION_FAILED, meal_type)
    except LLMError as exc:
        log.warning("composer.live.llm_error", meal_type=meal_type.value, error=str(exc))
        yield _failure_frame(error_event, str(exc), meal_type)


def _locked_sse(frames: AsyncIterator[dict[str, str]], *, run: str) -> EventSourceResponse:
    """Stream compose frames as SSE behind the shared lock, with the error backstop.

    A second trigger while one is in flight gets 409; the lock is the real guard, so
    even if two requests slip past the check they run one at a time, and it releases
    on completion or client cancel. The 200 and headers are already sent by the time
    an unexpected failure lands, so it cannot become an HTTP error: it has to close
    the open stream as a terminal ``error`` frame. A client disconnect raises
    CancelledError (a BaseException), so it still propagates rather than being
    swallowed.
    """
    if _compose_lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_GENERATION_BUSY)

    async def event_source() -> AsyncIterator[dict[str, str]]:
        async with _compose_lock:
            try:
                async for frame in frames:
                    yield frame
            except Exception:
                log.exception("composer.live.failed", run=run)
                yield {"event": "error", "data": json.dumps({"detail": _GENERATION_ERROR})}

    return EventSourceResponse(event_source())


def _compose_response(
    meal_type: MealType, streamer: ComposerStreamer, *, persist: Persist
) -> EventSourceResponse:
    """Stream one composition as SSE, saving it as pending, behind the shared lock."""
    frames = _slot_frames(meal_type, streamer, persist, error_event="error")
    return _locked_sse(frames, run=meal_type.value)


@router.post("/curated")
@limiter.limit(llm_rate_limit)
async def compose_curated(
    request: Request,
    payload: ComposeRequest,
    _admin: User = Depends(require_admin),
    streamer: ComposerStreamer = Depends(get_composer_streamer),
    embedder: Embedder = Depends(get_embedder),
) -> EventSourceResponse:
    """Stream one composition and save it to the curated pool as pending review."""

    async def persist(meal: ComposedMeal, session: AsyncSession) -> UUID:
        row = await MealService(session, embedder).store_pending(meal)
        await session.flush()
        return row.id

    return _compose_response(payload.meal_type, streamer, persist=persist)


@router.post("/daily")
@limiter.limit(llm_rate_limit)
async def compose_daily(
    request: Request,
    payload: ComposeDailyRequest,
    _admin: User = Depends(require_admin),
    streamer: ComposerStreamer = Depends(get_composer_streamer),
    daily: DailyService = Depends(get_daily_service),
) -> EventSourceResponse:
    """Compose one daily slot and save it as pending, refusing a silent overwrite.

    A slot already holding a pending or approved suggestion is a 409 unless the request
    carries ``replace=true``: the refusal happens before composing, so it spends no
    tokens, and an overwrite is never accidental. A rejected or empty slot proceeds.

    The pre-compose check is racy on its own, since the serialize lock is taken later
    inside the stream; the persist callback re-reads the slot under the lock and refuses a
    second time. That re-check, not the unique constraint, closes the clobber window.
    """
    now = datetime.now(UTC)
    _ensure_within_queue_window(payload.date, now)
    conflict = _conflict_detail(await daily.slot_for(payload.date, payload.meal_type), payload)
    if conflict is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict)

    async def persist(meal: ComposedMeal, session: AsyncSession) -> UUID:
        service = DailyService(session)
        # Re-read under the lock: the pre-check ran before the stream took the lock, so a
        # concurrent run could have filled the slot since. This is the real TOCTOU guard.
        if _conflict_detail(await service.slot_for(payload.date, payload.meal_type), payload):
            raise RuntimeError("The daily slot was filled by a concurrent run.")
        row = await service.store_pending(meal, payload.date, now=now)
        await session.flush()
        return row.id

    return _compose_response(payload.meal_type, streamer, persist=persist)


async def _board_frames(
    target: date, open_types: list[MealType], streamer: ComposerStreamer, *, now: datetime
) -> AsyncIterator[dict[str, str]]:
    """Compose the date's open slots in sequence, announcing each with ``slot``.

    ``open_types`` comes from the route's pre-check, which runs before the lock is
    taken, so it is racy on its own; as in the single-slot route, the persist callback
    re-reads the slot on the stream's session and refuses a concurrent fill. That
    refusal surfaces as the slot's ``slot_error`` and the run moves on.
    """

    async def persist(meal: ComposedMeal, session: AsyncSession) -> UUID:
        service = DailyService(session)
        if meal.meal_type not in await service.open_meal_types(target):
            raise RuntimeError("The daily slot was filled by a concurrent run.")
        row = await service.store_pending(meal, target, now=now)
        await session.flush()
        return row.id

    composed: list[MealType] = []
    failed: list[MealType] = []
    for index, meal_type in enumerate(open_types, start=1):
        start = SlotStartEvent(meal_type=meal_type, index=index, total=len(open_types))
        yield {"event": "slot", "data": start.model_dump_json()}
        saved = False
        async for frame in _slot_frames(meal_type, streamer, persist, error_event="slot_error"):
            yield frame
            saved = saved or frame["event"] == "saved"
        (composed if saved else failed).append(meal_type)

    skipped = [meal_type for meal_type in MealType if meal_type not in open_types]
    summary = BoardSummaryEvent(composed=composed, failed=failed, skipped=skipped)
    yield {"event": "board", "data": summary.model_dump_json()}


@router.post("/daily/board")
@limiter.limit(llm_rate_limit)
async def compose_daily_board(
    request: Request,
    payload: ComposeBoardRequest,
    _admin: User = Depends(require_admin),
    streamer: ComposerStreamer = Depends(get_composer_streamer),
    daily: DailyService = Depends(get_daily_service),
) -> EventSourceResponse:
    """Compose every open slot of one date in a single stream, saving each as pending.

    Board mode fills only slots that are empty or rejected; a pending or approved slot
    is never replaced, so a board run cannot destroy review work. A date with no open
    slot is a 409 before any tokens are spent. The whole board runs under one hold of
    the compose lock, so the nightly cron or a second trigger cannot interleave.
    """
    now = datetime.now(UTC)
    _ensure_within_queue_window(payload.date, now)
    open_types = await daily.open_meal_types(payload.date)
    if not open_types:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_BOARD_FULL)
    return _locked_sse(_board_frames(payload.date, open_types, streamer, now=now), run="board")


@router.get("/settings", response_model=ComposeSettingsRead)
async def read_settings(
    _admin: User = Depends(require_admin),
    service: GenerationSettingsService = Depends(get_generation_settings_service),
) -> ComposeSettingsRead:
    """Return the operator-set composer provider/model and the providers available."""
    row = await service.get()
    return ComposeSettingsRead(
        provider=row.composer_provider,
        model=row.composer_model,
        available_providers=selectable_providers(),
    )


@router.put("/settings", response_model=ComposeSettingsRead)
async def update_settings(
    payload: ComposeSettingsUpdate,
    admin: User = Depends(require_admin),
    service: GenerationSettingsService = Depends(get_generation_settings_service),
) -> ComposeSettingsRead:
    """Set the composer provider/model, validated through the provider truth source.

    Running the choice through ``resolve_llm_config`` rejects a keyless or gated
    provider (mapped to 400/501 at the boundary) before it can be persisted, so the
    saved setting is always usable and the provider rules cannot drift.
    """
    resolve_llm_config(LLMRequestConfig(provider=payload.provider.value, model=payload.model))
    row = await service.update(payload.provider.value, payload.model, actor=admin.email)
    return ComposeSettingsRead(
        provider=row.composer_provider,
        model=row.composer_model,
        available_providers=selectable_providers(),
    )
