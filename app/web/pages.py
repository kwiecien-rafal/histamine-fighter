"""The landing page, the two legal pages, and robots.txt."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.dependencies import get_daily_service, get_meal_service
from app.services.daily_service import DailyService
from app.services.meal_service import MealService
from app.web.deps import templates

router = APIRouter()

ROBOTS_TXT = "User-agent: *\nAllow: /\nDisallow: /admin\n"


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    daily_service: DailyService = Depends(get_daily_service),
    meal_service: MealService = Depends(get_meal_service),
) -> HTMLResponse:
    """The landing page: today's board strip, how the lookup works, and pool teasers.

    Two plain database reads and no LLM call, so the first impression is instant.
    """
    now = datetime.now(UTC)
    board = await daily_service.board_for(now.date(), now=now)
    approved_total = await meal_service.count_approved()
    return templates.TemplateResponse(
        request, "home.html", {"board": board, "approved_total": approved_total}
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    """The privacy policy."""
    return templates.TemplateResponse(request, "privacy.html", {})


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    """The terms of service."""
    return templates.TemplateResponse(request, "terms.html", {})


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> PlainTextResponse:
    """Crawler rules, served from the root because that is the only place crawlers look."""
    return PlainTextResponse(ROBOTS_TXT)
