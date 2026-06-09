from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from job_agent.web.dependencies import templates, workflow_handler

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "dashboard.html", {"request": request, **workflow_handler().executor.dashboard_view()}
    )
