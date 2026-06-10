from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from job_agent.web.dependencies import templates, workflow_handler

router = APIRouter()


@router.get("/setup-guide", response_class=HTMLResponse)
def setup_guide(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "setup_guide.html",
        {"request": request, "title": "Setup Guide", "setup_guide_page": workflow_handler().guide.context()},
    )


@router.post("/api/setup-guide/dismiss")
def dismiss_setup_guide(return_to: str = Form("/")) -> RedirectResponse:
    workflow_handler().guide.dismiss_guide()
    return RedirectResponse(url=_safe_return_to(return_to), status_code=303)


@router.post("/api/setup-guide/steps/{step_id}/dismiss")
def dismiss_setup_guide_step(step_id: str, return_to: str = Form("/")) -> RedirectResponse:
    try:
        workflow_handler().guide.dismiss_step(step_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Guide step not found") from exc
    return RedirectResponse(url=_safe_return_to(return_to), status_code=303)


@router.post("/api/setup-guide/reset")
def reset_setup_guide(return_to: str = Form("/setup-guide")) -> RedirectResponse:
    workflow_handler().guide.reset()
    return RedirectResponse(url=_safe_return_to(return_to), status_code=303)


def _safe_return_to(value: str) -> str:
    target = value.strip() or "/"
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target
