from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from job_agent.web.dependencies import templates, workflow_handler

router = APIRouter()


@router.get("/applications", response_class=HTMLResponse)
def applications_page(request: Request) -> HTMLResponse:
    filters = {
        "outcome": request.query_params.get("outcome", ""),
        "communication": request.query_params.get("communication", ""),
        "q": request.query_params.get("q", ""),
    }
    return templates.TemplateResponse(
        request,
        "applications.html",
        {"request": request, **workflow_handler().applications.list_view(filters)},
    )


@router.get("/applications/{application_id}", response_class=HTMLResponse)
def application_detail(
    request: Request,
    application_id: str,
    message: str = "",
    warning: str = "",
) -> HTMLResponse:
    try:
        view = workflow_handler().applications.detail_view(application_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Application not found") from None
    return templates.TemplateResponse(
        request,
        "application_detail.html",
        {"request": request, **view, "message": message, "warning": warning},
    )


@router.post("/api/applications/{application_id}/outcome")
def update_application_outcome(
    application_id: str,
    outcome: str = Form(...),
) -> RedirectResponse:
    try:
        workflow_handler().applications.update_outcome(application_id, outcome)
    except KeyError:
        raise HTTPException(status_code=404, detail="Application not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _redirect(application_id, message="Outcome saved.")


@router.post("/api/applications/{application_id}/gmail-thread/manual-link")
def manual_link_gmail_thread(
    application_id: str,
    thread_id: str = Form(...),
    account_id: str = Form(""),
) -> RedirectResponse:
    try:
        workflow_handler().applications.link_gmail_thread(application_id, thread_id, account_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Application not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _redirect(application_id, message="Gmail thread linked.")


@router.post("/api/applications/{application_id}/gmail-thread/{link_id}/unlink")
def unlink_gmail_thread(application_id: str, link_id: str) -> RedirectResponse:
    try:
        workflow_handler().applications.unlink_thread(application_id, link_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Thread link not found") from None
    return _redirect(application_id, message="Gmail thread unlinked.")


@router.post("/api/applications/{application_id}/gmail-thread/{link_id}/reject")
def reject_gmail_thread(
    application_id: str,
    link_id: str,
    rejected_reason: str = Form(""),
) -> RedirectResponse:
    try:
        workflow_handler().applications.reject_thread(application_id, link_id, rejected_reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="Thread link not found") from None
    return _redirect(application_id, message="Gmail thread rejected.")


@router.post("/api/applications/{application_id}/gmail-thread/{link_id}/reassign")
def reassign_gmail_thread(
    application_id: str,
    link_id: str,
    target_application_id: str = Form(...),
) -> RedirectResponse:
    try:
        workflow_handler().applications.reassign_thread(application_id, link_id, target_application_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Thread link or target application not found") from None
    return _redirect(target_application_id, message="Gmail thread reassigned.")


@router.post("/api/applications/{application_id}/manual-event")
def add_manual_event(
    application_id: str,
    channel: str = Form("email"),
    direction: str = Form("note"),
    occurred_at: str = Form(""),
    contact: str = Form(""),
    subject: str = Form(""),
    note: str = Form(""),
) -> RedirectResponse:
    try:
        workflow_handler().applications.add_manual_event(
            application_id,
            channel=channel,
            direction=direction,
            occurred_at=occurred_at,
            contact=contact,
            subject=subject,
            note=note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Application not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _redirect(application_id, message="Communication event added.")


def _redirect(application_id: str, *, message: str = "", warning: str = "") -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if warning:
        params["warning"] = warning
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"/applications/{application_id}{query}#communication", status_code=303)
