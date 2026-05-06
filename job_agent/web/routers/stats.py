from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from job_agent.web.dependencies import templates
from job_agent.web.view_models.stats import build_stats_view

router = APIRouter()


@router.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "stats.html", {"request": request, **build_stats_view()})
