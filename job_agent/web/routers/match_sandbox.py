from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from job_agent.web.dependencies import current_root, setup_service, templates
from job_agent.web.view_models.match_sandbox import build_match_sandbox_view

router = APIRouter()


@router.get("/match-sandbox", response_class=HTMLResponse)
def match_sandbox(request: Request, job_id: str = "", run_id: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "match_sandbox.html",
        {"request": request, **build_match_sandbox_view(current_root(), job_id=job_id, run_id=run_id)},
    )


@router.post("/match-sandbox", response_class=HTMLResponse)
async def score_match_sandbox(request: Request) -> HTMLResponse:
    form = await request.form()
    return templates.TemplateResponse(
        request,
        "match_sandbox.html",
        {"request": request, **build_match_sandbox_view(current_root(), form=form)},
    )


@router.post("/match-sandbox/save", response_class=HTMLResponse)
async def save_match_sandbox_settings(request: Request) -> HTMLResponse:
    form = await request.form()
    setup_service().save_match_engine_settings_from_form(form)
    return templates.TemplateResponse(
        request,
        "match_sandbox.html",
        {"request": request, **build_match_sandbox_view(current_root(), form=form, settings_saved=True)},
    )


@router.post("/api/match-sandbox/score")
async def api_score_match_sandbox(request: Request) -> dict:
    return setup_service().score_sandbox_form(await request.form())

