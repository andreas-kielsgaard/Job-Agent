from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from job_agent.application_status_store import APPLICATION_STATUSES
from job_agent.llm import ExternalAgentService, LlmRequest
from job_agent.services.material_service import MaterialUpdate
from job_agent.services.review_bundle_service import ReviewBundleService
from job_agent.web.dependencies import (
    application_status_store,
    current_root,
    material_service,
    package_service,
    templates,
)
from job_agent.web.view_models.jobs import build_job_detail_view, build_jobs_view

router = APIRouter()


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request) -> HTMLResponse:
    filters = {
        "app_status_includes": _filter_values(request, "app_status_include", legacy="app_status"),
        "app_status_excludes": _filter_values(request, "app_status_exclude"),
        "category_includes": _filter_values(request, "category_include", legacy="category"),
        "category_excludes": _filter_values(request, "category_exclude"),
        "source_id_includes": _filter_values(request, "source_id_include", legacy="source_id"),
        "source_id_excludes": _filter_values(request, "source_id_exclude"),
        "run_id_includes": _filter_values(request, "run_id_include", legacy="run_id"),
        "run_id_excludes": _filter_values(request, "run_id_exclude"),
        "date_from": request.query_params.get("date_from", ""),
        "date_to": request.query_params.get("date_to", ""),
        "source": request.query_params.get("source", ""),
        "material_status_includes": _filter_values(request, "material_status_include", legacy="material_status"),
        "material_status_excludes": _filter_values(request, "material_status_exclude"),
        "posting_status_includes": _filter_values(request, "posting_status_include"),
        "posting_status_excludes": _filter_values(request, "posting_status_exclude"),
        "ai_prioritized": bool(request.query_params.get("ai_prioritized")),
        "dedupe": request.query_params.get("dedupe", "1") != "0",
    }
    return_to = str(request.url.path)
    if request.url.query:
        return_to = f"{return_to}?{request.url.query}"
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"request": request, **build_jobs_view(filters, current_root()), "return_to": return_to},
    )


def _filter_values(request: Request, name: str, *, legacy: str = "") -> list[str]:
    values = [value for value in request.query_params.getlist(name) if value]
    if legacy:
        values.extend(value for value in request.query_params.getlist(legacy) if value)
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


@router.post("/api/jobs/bulk-status")
def bulk_job_status(
    job_ids: list[str] = Form(...), status: str = Form(...), return_to: str = Form("/jobs")
) -> RedirectResponse:
    if status not in APPLICATION_STATUSES:
        raise HTTPException(status_code=400, detail="Unsupported application status")
    status_store = application_status_store()
    packages = package_service()
    for job_id in job_ids:
        try:
            status_store.update_status(job_id, status)
            packages.refresh_package_status(job_id, status)
        except (KeyError, ValueError):
            continue
    return RedirectResponse(url=return_to, status_code=303)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str, run_id: str = "") -> HTMLResponse:
    try:
        view = build_job_detail_view(job_id, run_id, current_root())
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
    application_status_store().update_status(
        job_id,
        status,
        notes=notes,
        not_interesting_reason=not_interesting_reason,
    )
    package_service().refresh_package_status(job_id, status)
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
        material_service().save_job_materials(
            job_id,
            MaterialUpdate(cv=cv, application=application, form_answers=form_answers, match_analysis=match_analysis),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Job package not found") from None
    return RedirectResponse(url=return_to or f"/jobs/{job_id}", status_code=303)


@router.post("/api/jobs/{job_id}/generate")
def generate_job_materials(job_id: str, use_llm: bool = Form(False), return_to: str = Form("")) -> RedirectResponse:
    try:
        material_service().generate_job_materials(job_id, use_llm)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job package not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=return_to or f"/jobs/{job_id}", status_code=303)


@router.post("/api/jobs/{job_id}/review-bundle/external-agent/prepare")
def prepare_external_review_bundle(job_id: str, run_id: str = Form("")) -> JSONResponse:
    root = current_root()
    package = package_service().find_package(job_id, run_id)
    if not package:
        return JSONResponse({"ok": False, "error": "Job package not found"}, status_code=404)
    files = package_service().read_package_files(package)
    status = application_status_store().get(job_id)
    bundle = ReviewBundleService(root).build(package, files, status)
    interaction = ExternalAgentService(root).prepare(
        LlmRequest(
            prompt=bundle,
            max_tokens=0,
            purpose="external_review_bundle",
            run_id=package.get("run_id", ""),
            associated_job_id=package.get("stable_id", ""),
        ),
        title=f"Review bundle for {package.get('title', 'job')}",
        instructions="Copy this bundle into an external agent when you want a second opinion. It does not update app files by itself.",
        metadata={"job_id": job_id, "run_id": package.get("run_id", "")},
    )
    payload = interaction.to_payload()
    payload["response_mode"] = "none"
    return JSONResponse({"ok": True, **payload})


@router.post("/api/jobs/{job_id}/application/external-agent/prepare")
def prepare_external_application_generation(
    job_id: str,
    run_id: str = Form(""),
) -> JSONResponse:
    try:
        payload = material_service().prepare_external_application_generation(job_id, run_id)
    except KeyError:
        return JSONResponse({"ok": False, "error": "Job package not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, **payload})


@router.post("/api/jobs/{job_id}/application/external-agent/apply")
def apply_external_application_generation(
    job_id: str,
    interaction_id: str = Form(...),
    response_text: str = Form(...),
    run_id: str = Form(""),
    return_to: str = Form(""),
) -> JSONResponse:
    try:
        material_service().apply_external_application_generation(job_id, interaction_id, response_text, run_id)
    except KeyError:
        return JSONResponse({"ok": False, "error": "Job package not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "redirect_url": return_to or f"/jobs/{job_id}"})


@router.post("/api/jobs/batch-generate")
def batch_generate_job_materials(
    job_ids: list[str] = Form(default=[]),
    use_llm: bool = Form(False),
    return_to: str = Form("/jobs"),
) -> RedirectResponse:
    result = material_service().generate_many(job_ids, use_llm)
    separator = "&" if "?" in return_to else "?"
    query = urlencode({"generated": result.succeeded, "failed": result.failed})
    return RedirectResponse(url=f"{return_to}{separator}{query}", status_code=303)
