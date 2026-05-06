from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.services.material_service import MaterialService, MaterialUpdate
from job_agent.services.package_index_service import PackageIndexService
from job_agent.web.dependencies import templates
from job_agent.web.view_models.jobs import build_job_detail_view, build_jobs_view

router = APIRouter()


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request) -> HTMLResponse:
    app_statuses = request.query_params.getlist("app_status")
    categories = request.query_params.getlist("category")
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"request": request, **build_jobs_view(app_statuses, categories)},
    )


@router.post("/api/jobs/bulk-status")
def bulk_job_status(job_ids: list[str] = Form(...), status: str = Form(...), return_to: str = Form("/jobs")) -> RedirectResponse:
    status_store = ApplicationStatusStore(ROOT)
    package_service = PackageIndexService(ROOT)
    for job_id in job_ids:
        try:
            status_store.update_status(job_id, status)
            package_service.refresh_package_status(job_id, status)
        except (KeyError, ValueError):
            continue
    return RedirectResponse(url=return_to, status_code=303)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str, run_id: str = "") -> HTMLResponse:
    try:
        view = build_job_detail_view(job_id, run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job package not found") from None
    return templates.TemplateResponse(request, "job_detail.html", {"request": request, **view})


@router.post("/api/jobs/{job_id}/status")
def update_job_status(
    job_id: str,
    status: str = Form(...),
    notes: str = Form(""),
    not_interesting_reason: str = Form(""),
    return_to: str = Form(""),
) -> RedirectResponse:
    ApplicationStatusStore(ROOT).update_status(
        job_id,
        status,
        notes=notes,
        not_interesting_reason=not_interesting_reason,
    )
    PackageIndexService(ROOT).refresh_package_status(job_id, status)
    return RedirectResponse(url=return_to or f"/jobs/{job_id}", status_code=303)


@router.post("/api/jobs/{job_id}/materials")
def save_job_materials(
    job_id: str,
    cv: str = Form(""),
    application: str = Form(""),
    form_answers: str = Form(""),
    match_analysis: str = Form(""),
    return_to: str = Form(""),
) -> RedirectResponse:
    try:
        MaterialService(ROOT).save_job_materials(
            job_id,
            MaterialUpdate(cv=cv, application=application, form_answers=form_answers, match_analysis=match_analysis),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Job package not found") from None
    return RedirectResponse(url=return_to or f"/jobs/{job_id}", status_code=303)


@router.post("/api/jobs/{job_id}/generate")
def generate_job_materials(job_id: str, use_llm: bool = Form(False), return_to: str = Form("")) -> RedirectResponse:
    try:
        MaterialService(ROOT).generate_job_materials(job_id, use_llm)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job package not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=return_to or f"/jobs/{job_id}", status_code=303)
