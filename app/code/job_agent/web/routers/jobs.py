from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from job_agent.application_status_store import APPLICATION_STATUSES
from job_agent.llm import ExternalAgentService, LlmRequest
from job_agent.paths import output_dir
from job_agent.services.job_context_copy_service import JobContextCopyService
from job_agent.services.match_update_service import MatchUpdateService
from job_agent.services.material_service import MaterialUpdate
from job_agent.services.review_bundle_service import ReviewBundleService
from job_agent.web.dependencies import (
    application_status_store,
    application_tracker_service,
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
        "show_condition_excluded": bool(request.query_params.get("show_condition_excluded")),
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


def _unique_text_values(values: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    items = values if isinstance(values, list) else []
    for value in items:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _set_job_application_status(
    job_id: str,
    status: str,
    *,
    notes: str | None = None,
    not_interesting_reason: str | None = None,
) -> None:
    store = application_status_store()
    try:
        store.update_status(job_id, status, notes=notes, not_interesting_reason=not_interesting_reason)
    except KeyError:
        package = package_service().find_package(job_id)
        if not package:
            raise
        store.ensure_for_job(
            stable_id=str(package.get("stable_id") or package.get("package_id") or job_id),
            fuzzy_key=str(package.get("fuzzy_key") or ""),
            title=str(package.get("title") or "Untitled job"),
            company=str(package.get("company") or "Unknown"),
            source=str(package.get("source") or package.get("source_id") or "Unknown"),
            url=str(package.get("source_url") or package.get("url") or ""),
            application_url=str(
                package.get("application_url") or package.get("source_url") or package.get("url") or ""
            ),
        )
        store.update_status(job_id, status, notes=notes, not_interesting_reason=not_interesting_reason)
    package_service().refresh_package_status(job_id, status)
    if status == "applied":
        application_tracker_service().ensure_from_job(job_id)


@router.post("/api/jobs/bulk-status")
def bulk_job_status(
    job_ids: list[str] = Form(...), status: str = Form(...), return_to: str = Form("/jobs")
) -> RedirectResponse:
    if status not in APPLICATION_STATUSES:
        raise HTTPException(status_code=400, detail="Unsupported application status")
    for job_id in job_ids:
        try:
            _set_job_application_status(job_id, status)
        except (KeyError, ValueError):
            continue
    return RedirectResponse(url=return_to, status_code=303)


@router.post("/api/jobs/status")
async def update_jobs_status(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"ok": False, "error": "Invalid JSON payload"}, status_code=400)
    status = str(payload.get("status") or "")
    if status not in APPLICATION_STATUSES:
        return JSONResponse({"ok": False, "error": "Unsupported application status"}, status_code=400)
    raw_job_ids = payload.get("job_ids") or []
    if isinstance(raw_job_ids, str):
        raw_job_ids = [raw_job_ids]
    job_ids = _unique_text_values(raw_job_ids)
    if not job_ids:
        return JSONResponse({"ok": False, "error": "No jobs selected"}, status_code=400)

    updated_ids: list[str] = []
    failed_ids: list[str] = []
    for job_id in job_ids:
        try:
            _set_job_application_status(job_id, status)
            updated_ids.append(job_id)
        except (KeyError, ValueError):
            failed_ids.append(job_id)
    return JSONResponse(
        {
            "ok": bool(updated_ids),
            "status": status,
            "updated_ids": updated_ids,
            "failed_ids": failed_ids,
            "error": "" if updated_ids else "No selected jobs could be updated",
        },
        status_code=200 if updated_ids else 404,
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str, run_id: str = "", status_saved: str = "") -> HTMLResponse:
    try:
        view = build_job_detail_view(job_id, run_id, current_root())
    except KeyError:
        raise HTTPException(status_code=404, detail="Job package not found") from None
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {"request": request, **view, "status_saved": status_saved},
    )


@router.get("/jobs/{job_id}/files/{file_key}")
def job_package_file(job_id: str, file_key: str, run_id: str = "", download: bool = False) -> FileResponse:
    allowed = {
        "posting_snapshot": "text/markdown",
        "focused_cv_html": "text/html",
        "focused_cv": "text/markdown",
        "focused_cv_tex": "text/x-tex",
        "focused_cv_pdf": "application/pdf",
        "cv": "text/markdown",
        "application": "text/markdown",
        "form_answers": "text/markdown",
        "match_analysis": "text/markdown",
    }
    if file_key not in allowed:
        raise HTTPException(status_code=404, detail="Generated file not found")
    package = package_service().find_package(job_id, run_id)
    if not package:
        raise HTTPException(status_code=404, detail="Job package not found")
    path_text = str(package.get("paths", {}).get(file_key) or "")
    if not path_text:
        raise HTTPException(status_code=404, detail="Generated file not found")
    path = Path(path_text).resolve()
    try:
        path.relative_to(output_dir(current_root()).resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Generated file not found") from None
    if not path.exists():
        raise HTTPException(status_code=404, detail="Generated file not found")
    return FileResponse(
        path,
        filename=path.name,
        media_type=allowed[file_key],
        content_disposition_type="attachment" if download else "inline",
    )


@router.post("/api/jobs/{job_id}/status")
def update_job_status(
    job_id: str,
    status: str = Form(...),
    notes: str = Form(""),
    not_interesting_reason: str = Form(""),
    return_to: str = Form(""),
) -> RedirectResponse:
    _set_job_application_status(
        job_id,
        status,
        notes=notes,
        not_interesting_reason=not_interesting_reason,
    )
    target = _url_with_query(return_to or f"/jobs/{job_id}", status_saved=status)
    return RedirectResponse(url=target, status_code=303)


def _url_with_query(url: str, **params: str) -> str:
    split = urlsplit(url)
    existing = [(key, value) for key, value in parse_qsl(split.query, keep_blank_values=True) if key not in params]
    additions = [(key, value) for key, value in params.items() if value]
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode([*existing, *additions]), split.fragment))


@router.post("/api/jobs/{job_id}/materials")
async def save_job_materials(request: Request, job_id: str) -> RedirectResponse:
    form = await request.form()
    material_fields = {"cv", "focused_cv", "application", "form_answers", "match_analysis"}
    submitted_fields = {key for key in material_fields if key in form}
    try:
        material_service().save_job_materials(
            job_id,
            MaterialUpdate(
                cv=str(form.get("cv", "")),
                focused_cv=str(form.get("focused_cv", "")),
                application=str(form.get("application", "")),
                form_answers=str(form.get("form_answers", "")),
                match_analysis=str(form.get("match_analysis", "")),
            ),
            run_id=str(form.get("run_id", "")),
            fields=submitted_fields,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Job package not found") from None
    return RedirectResponse(url=str(form.get("return_to", "")) or f"/jobs/{job_id}", status_code=303)


@router.post("/api/jobs/{job_id}/generate")
def generate_job_materials(
    job_id: str,
    use_llm: bool = Form(False),
    run_id: str = Form(""),
    return_to: str = Form(""),
    llm_model: str = Form(""),
) -> RedirectResponse:
    try:
        material_service().generate_job_materials(job_id, use_llm, run_id=run_id, llm_model=llm_model)
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


@router.post("/api/jobs/{job_id}/context/copy")
def copy_job_context(job_id: str, run_id: str = Form("")) -> JSONResponse:
    root = current_root()
    package = package_service().find_package(job_id, run_id)
    if not package:
        return JSONResponse({"ok": False, "error": "Job package not found"}, status_code=404)
    files = package_service().read_package_files(package)
    status = application_status_store().get(job_id)
    context = JobContextCopyService(root).build(package, files, status)
    return JSONResponse({"ok": True, "context": context, "title": f"Context for {package.get('title', 'job')}"})


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
    llm_model: str = Form(""),
) -> RedirectResponse:
    result = material_service().generate_many(job_ids, use_llm, llm_model=llm_model)
    separator = "&" if "?" in return_to else "?"
    query = urlencode({"generated": result.succeeded, "failed": result.failed})
    return RedirectResponse(url=f"{return_to}{separator}{query}", status_code=303)


@router.post("/api/jobs/recalculate-matches")
def recalculate_filtered_matches(return_to: str = Form("/jobs")) -> RedirectResponse:
    packages = _filtered_package_rows(return_to)
    result = MatchUpdateService(current_root()).recalculate_deterministic(packages)
    return RedirectResponse(
        url=_url_with_query(
            return_to,
            recalculated=str(result.updated),
            skipped=str(result.skipped),
            failed=str(result.failed),
        ),
        status_code=303,
    )


@router.post("/api/jobs/apply-ai-matching")
def apply_ai_matching_to_filtered_jobs(
    return_to: str = Form("/jobs"),
    llm_model: str = Form(""),
) -> RedirectResponse:
    packages = _filtered_package_rows(return_to)
    try:
        result = MatchUpdateService(current_root()).apply_ai_matching(packages, llm_model=llm_model)
    except ValueError as exc:
        return RedirectResponse(url=_url_with_query(return_to, warning=str(exc)), status_code=303)
    return RedirectResponse(
        url=_url_with_query(
            return_to,
            ai_matched=str(result.updated),
            skipped=str(result.skipped),
            failed=str(result.failed),
        ),
        status_code=303,
    )


def _filtered_package_rows(return_to: str) -> list[dict]:
    filters = _filters_from_url(return_to)
    view = build_jobs_view(filters, current_root())
    rows = view.get("jobs", [])
    return [row for row in rows if row.get("has_package")]


def _filters_from_url(url: str) -> dict[str, object]:
    query = dict_list(urlsplit(url).query)
    return {
        "app_status_includes": _query_values(query, "app_status_include", legacy="app_status"),
        "app_status_excludes": _query_values(query, "app_status_exclude"),
        "category_includes": _query_values(query, "category_include", legacy="category"),
        "category_excludes": _query_values(query, "category_exclude"),
        "source_id_includes": _query_values(query, "source_id_include", legacy="source_id"),
        "source_id_excludes": _query_values(query, "source_id_exclude"),
        "run_id_includes": _query_values(query, "run_id_include", legacy="run_id"),
        "run_id_excludes": _query_values(query, "run_id_exclude"),
        "date_from": (query.get("date_from") or [""])[0],
        "date_to": (query.get("date_to") or [""])[0],
        "source": (query.get("source") or [""])[0],
        "material_status_includes": _query_values(query, "material_status_include", legacy="material_status"),
        "material_status_excludes": _query_values(query, "material_status_exclude"),
        "posting_status_includes": _query_values(query, "posting_status_include"),
        "posting_status_excludes": _query_values(query, "posting_status_exclude"),
        "ai_prioritized": bool(query.get("ai_prioritized")),
        "show_condition_excluded": bool(query.get("show_condition_excluded")),
        "dedupe": (query.get("dedupe") or ["1"])[0] != "0",
    }


def dict_list(query: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        result.setdefault(key, []).append(value)
    return result


def _query_values(query: dict[str, list[str]], name: str, *, legacy: str = "") -> list[str]:
    values = [value for value in query.get(name, []) if value]
    if legacy:
        values.extend(value for value in query.get(legacy, []) if value)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result
