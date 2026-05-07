from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from job_agent.services.job_board_check_service import check_job_board_compatibility
from job_agent.web.dependencies import templates

router = APIRouter()


@router.get("/compatibility", response_class=HTMLResponse)
def compatibility_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "compatibility.html", {"request": request, "report": None})


@router.post("/compatibility", response_class=HTMLResponse)
def run_compatibility_check(
    request: Request,
    url: str = Form(""),
    render: bool = Form(False),
) -> HTMLResponse:
    try:
        report = check_job_board_compatibility(url, render=render)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "compatibility.html",
        {"request": request, "report": report, "url": url, "render": render},
    )
