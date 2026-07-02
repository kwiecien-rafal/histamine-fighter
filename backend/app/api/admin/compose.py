"""Admin compose: stream a live composition into a pending row, and set the model.

Every route is gated by ``require_admin``. The two streaming routes share one
process-local lock so an operator's repeated triggers cannot overlap into several
concurrent multi-call LLM runs within a worker (per-worker, see ``_compose_lock``).
Each persists the finished, trace-carrying meal as pending review and confirms it with
a ``saved`` frame: curated into the pool, daily into a dated slot. The settings routes
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
    ComposeDailyRequest,
    ComposeRequest,
    ComposeSettingsRead,
    ComposeSettingsUpdate,
)
from app.schemas.meal import ComposedMeal
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


def _compose_response(
    meal_type: MealType, streamer: ComposerStreamer, *, persist: Persist
) -> EventSourceResponse:
    """Stream one composition as SSE, saving it as pending, behind the shared lock.

    A second trigger while one is in flight gets 409. The stream relays the streamer's
    frames and turns a compose failure into a terminal ``error`` frame, so an already
    open stream closes cleanly rather than the client reading a truncated stream as
    success.
    """
    if _compose_lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_GENERATION_BUSY)

    async def event_source() -> AsyncIterator[dict[str, str]]:
        # The lock is the real guard: even if two requests slip past the check above,
        # they run one at a time, and it releases here on completion or client cancel.
        async with _compose_lock:
            try:
                async for frame in streamer.stream(meal_type, persist=persist):
                    yield frame
                    if frame["event"] == "meal":
                        _log_done(meal_type.value, json.loads(frame["data"]))
            except ComposerExhausted:
                log.warning("composer.live.exhausted", meal_type=meal_type.value)
                yield {"event": "error", "data": json.dumps({"detail": _GENERATION_FAILED})}
            except LLMError as exc:
                log.warning("composer.live.llm_error", meal_type=meal_type.value, error=str(exc))
                yield {"event": "error", "data": json.dumps({"detail": str(exc)})}
            except Exception:
                # The 200 and headers are already sent, so an unexpected failure cannot
                # become an HTTP error: it has to close the stream as an error event. A
                # client disconnect raises CancelledError (a BaseException), so it still
                # propagates here rather than being swallowed.
                log.exception("composer.live.failed", meal_type=meal_type.value)
                yield {"event": "error", "data": json.dumps({"detail": _GENERATION_ERROR})}

    return EventSourceResponse(event_source())


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
