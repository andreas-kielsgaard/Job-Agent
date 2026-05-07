from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from job_agent.web.dependencies import current_root, templates
from job_agent.web.view_models.dashboard import build_dashboard_view

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "dashboard.html", {"request": request, **build_dashboard_view(current_root())}
    )
