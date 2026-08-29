"""The Learn hub."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.dependencies import get_knowledge_service
from app.services.knowledge_service import KnowledgeService
from app.web.deps import templates

router = APIRouter()


@router.get("/learn", response_class=HTMLResponse)
async def learn_hub(
    request: Request,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> HTMLResponse:
    """The topic index of the curated knowledge corpus, grouped by topic in the template."""
    return templates.TemplateResponse(request, "learn.html", {"articles": await service.topics()})
