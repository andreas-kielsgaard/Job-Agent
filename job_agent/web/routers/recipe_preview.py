from __future__ import annotations

import re

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from job_agent.services.recipe_preview_service import explain_recipe, preview_recipe
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_health_service import SourceHealthService
from job_agent.web.debug_state import record_debug_event
from job_agent.web.dependencies import current_root, templates
from job_agent.web.form_options import default_recipe_for_source, include_selected_recipe_option, recipe_options, source_options
from job_agent.web.source_workflow import SourceWorkflowHandler
from job_agent.web.view_models.source_debug import recipe_label, source_debug_option, source_debug_options

router = APIRouter()


@router.get("/recipe-preview", response_class=HTMLResponse)
def recipe_preview_form(
    request: Request,
    recipe_path: str = Query(""),
    input_path_or_url: str = Query(""),
    source_id: str = Query(""),
    source_mode: str = Query("configured"),
    selected_source_id: str = Query(""),
    tab: str = Query("execute"),
    auto_run: bool = Query(False),
) -> HTMLResponse:
    root = current_root()
    sources = source_options(root)
    recipes = recipe_options(root)
    source_key = selected_source_id.strip() or source_id.strip()
    selected_source = next((source for source in sources if source.id == source_key), None)
    normalized_source_mode = source_mode if source_mode in {"configured", "custom"} else "configured"
    normalized_tab = tab if tab in {"execute", "explain"} else "execute"
    recipe_path = recipe_path or default_recipe_for_source(selected_source, recipes)
    recipes = include_selected_recipe_option(recipes, recipe_path)
    if selected_source and not input_path_or_url:
        input_path_or_url = selected_source.url
    saved_health = SourceHealthService(root).get_health(selected_source.id) if selected_source else None
    readiness = SourceExecutionReadinessService(root).evaluate(selected_source.id) if selected_source else None
    source_review = (
        _source_review_from_readiness(readiness, selected_source, root=root)
        if selected_source and normalized_source_mode != "custom" and readiness
        else None
    )
    recipe_explanation = explain_recipe(recipe_path, root=root) if recipe_path else None
    use_latest_review = bool(source_review and source_review.get("worked"))
    record_debug_event(
        root,
        feature="recipe_preview",
        action="render_form",
        method=request.method,
        request_path=str(request.url),
        state=_recipe_preview_debug_state(
            recipe_path=recipe_path,
            input_path_or_url=input_path_or_url,
            source_mode=normalized_source_mode,
            selected_source=selected_source,
            selected_source_id=source_key,
            sources=sources,
            recipes=recipes,
            tab=normalized_tab,
            recipe_explanation=recipe_explanation,
        ),
    )
    response = templates.TemplateResponse(
        request,
        "recipe_preview.html",
        {
            "request": request,
            "title": f"Recipe Preview - {selected_source.name}" if selected_source else "Recipe Preview",
            "preview": None,
            "source_review": source_review,
            "recipe_explanation": recipe_explanation,
            "recipe_path": recipe_path,
            "input_path_or_url": input_path_or_url,
            "source_id": source_id,
            "source_mode": normalized_source_mode,
            "selected_source_id": source_key,
            "sources": sources,
            "recipe_options": recipes,
            "tab": normalized_tab,
            "auto_run": (
                auto_run
                and normalized_tab == "execute"
                and bool(recipe_path and input_path_or_url)
                and not use_latest_review
            ),
            "saved_health": saved_health,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/recipe-preview", response_class=HTMLResponse)
def run_recipe_preview(
    request: Request,
    recipe_path: str = Form(""),
    input_path_or_url: str = Form(""),
    source_mode: str = Form("configured"),
    selected_source_id: str = Form(""),
) -> HTMLResponse:
    root = current_root()
    sources = source_options(root)
    recipes = recipe_options(root)
    selected_source = None
    source_id = ""
    if source_mode == "configured" and selected_source_id.strip():
        selected_source = next((source for source in sources if source.id == selected_source_id.strip()), None)
        if selected_source:
            recipe_path = recipe_path or default_recipe_for_source(selected_source, recipes)
            input_path_or_url = input_path_or_url or selected_source.url
            source_id = source_id or selected_source.id
    recipes = include_selected_recipe_option(recipes, recipe_path)
    if selected_source and source_mode != "custom":
        execution = SourceWorkflowHandler(root).run_source_test(selected_source.id)
        recipe_explanation = explain_recipe(recipe_path, root=root) if recipe_path else None
        source_review = _source_review_from_payload(execution.payload, selected_source)
        readiness = execution.readiness
        saved_health = SourceHealthService(root).get_health(selected_source.id)
        record_debug_event(
            root,
            feature="recipe_preview",
            action="source_test_review_completed",
            method=request.method,
            request_path=str(request.url),
            state=_recipe_preview_debug_state(
                recipe_path=recipe_path,
                input_path_or_url=input_path_or_url,
                source_mode=source_mode,
                selected_source=selected_source,
                selected_source_id=selected_source.id,
                sources=sources,
                recipes=recipes,
                tab="execute",
                recipe_explanation=recipe_explanation,
                source_review=source_review,
                health_saved=False,
            ),
        )
        response = templates.TemplateResponse(
            request,
            "recipe_preview.html",
            {
                "request": request,
                "title": f"Recipe Preview - {selected_source.name}",
                "preview": None,
                "source_review": source_review,
                "recipe_explanation": recipe_explanation,
                "recipe_path": recipe_path,
                "input_path_or_url": input_path_or_url,
                "source_id": selected_source.id,
                "source_mode": "configured",
                "selected_source_id": selected_source.id,
                "sources": sources,
                "recipe_options": recipes,
                "health_saved": False,
                "tab": "execute",
                "auto_run": False,
                "saved_health": saved_health,
                "readiness": readiness,
            },
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    if not input_path_or_url.strip().startswith(("http://", "https://")):
        record_debug_event(
            root,
            feature="recipe_preview",
            action="validation_failed",
            method=request.method,
            request_path=str(request.url),
            state=_recipe_preview_debug_state(
                recipe_path=recipe_path,
                input_path_or_url=input_path_or_url,
                source_mode=source_mode,
                selected_source=selected_source,
                selected_source_id=selected_source_id,
                sources=sources,
                recipes=recipes,
                tab="execute",
                error="Enter a public http(s) source URL.",
            ),
        )
        raise HTTPException(status_code=400, detail="Enter a public http(s) source URL.")
    try:
        preview = preview_recipe(
            recipe_path,
            input_path_or_url,
            base_url="",
            rendered=False,
            static=False,
            detail_input_value="",
            root=current_root(),
        )
        health_saved = False
        if source_id.strip():
            SourceHealthService(current_root()).save_preview(source_id.strip(), preview)
            health_saved = True
    except ValueError as exc:
        if source_id.strip():
            SourceHealthService(current_root()).save_failure(
                source_id.strip(),
                input_path_or_url,
                "unknown",
                str(exc),
            )
        record_debug_event(
            root,
            feature="recipe_preview",
            action="preview_failed",
            method=request.method,
            request_path=str(request.url),
            state=_recipe_preview_debug_state(
                recipe_path=recipe_path,
                input_path_or_url=input_path_or_url,
                source_mode=source_mode,
                selected_source=selected_source,
                selected_source_id=selected_source_id,
                sources=sources,
                recipes=recipes,
                tab="execute",
                error=str(exc),
            ),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    recipe_explanation = explain_recipe(recipe_path, root=root) if recipe_path else None
    saved_health = SourceHealthService(root).get_health(source_id.strip()) if source_id.strip() else None
    record_debug_event(
        root,
        feature="recipe_preview",
        action="preview_completed",
        method=request.method,
        request_path=str(request.url),
        state=_recipe_preview_debug_state(
            recipe_path=recipe_path,
            input_path_or_url=input_path_or_url,
            source_mode=source_mode,
            selected_source=selected_source,
            selected_source_id=selected_source.id if selected_source else selected_source_id,
            sources=sources,
            recipes=recipes,
            tab="execute",
            recipe_explanation=recipe_explanation,
            preview=preview,
            health_saved=health_saved,
        ),
    )
    response = templates.TemplateResponse(
        request,
        "recipe_preview.html",
        {
            "request": request,
            "title": f"Recipe Preview - {selected_source.name}" if selected_source else "Recipe Preview",
            "preview": preview,
            "source_review": None,
            "recipe_explanation": recipe_explanation,
            "recipe_path": recipe_path,
            "input_path_or_url": input_path_or_url,
            "source_id": source_id,
            "source_mode": source_mode if source_mode in {"configured", "custom"} else "configured",
            "selected_source_id": selected_source.id if selected_source else selected_source_id,
            "sources": sources,
            "recipe_options": recipes,
            "health_saved": health_saved,
            "tab": "execute",
            "auto_run": False,
            "saved_health": saved_health,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _recipe_preview_debug_state(
    *,
    recipe_path: str,
    input_path_or_url: str,
    source_mode: str,
    selected_source,
    selected_source_id: str,
    sources: list,
    recipes: list[dict[str, str]],
    tab: str,
    recipe_explanation=None,
    preview=None,
    source_review=None,
    health_saved: bool = False,
    error: str = "",
) -> dict:
    state = {
        "source_mode": source_mode,
        "selected_source_id": selected_source.id if selected_source else selected_source_id,
        "selected_source": source_debug_option(selected_source) if selected_source else None,
        "input_path_or_url": input_path_or_url,
        "recipe_path": recipe_path,
        "recipe_label": recipe_label(recipe_path, recipes),
        "tab": tab,
        "health_saved": health_saved,
        "sources": source_debug_options(sources),
        "recipes": recipes,
        "error": error,
    }
    if recipe_explanation:
        state["recipe_explanation"] = {
            "source_name": recipe_explanation.source_name,
            "status": recipe_explanation.status,
            "start_url": recipe_explanation.start_url,
            "mode_label": recipe_explanation.mode_label,
            "detail_follow": recipe_explanation.detail_follow,
            "detail_max_pages": recipe_explanation.detail_max_pages,
            "detail_delay": recipe_explanation.detail_delay,
            "pagination_configured": recipe_explanation.pagination_configured,
            "pagination_max_pages": recipe_explanation.pagination_max_pages,
        }
    if preview:
        state["preview"] = {
            "recipe_source_name": preview.recipe_source_name,
            "recipe_status": preview.recipe_status,
            "input_type": preview.input_type,
            "mode_used": preview.mode_used,
            "extracted_job_count": preview.extracted_job_count,
            "useful_titles": preview.useful_titles,
            "generic_labels": preview.generic_labels,
            "unique_urls": preview.unique_urls,
            "average_description_length": preview.average_description_length,
            "pagination_configured": preview.pagination_configured,
            "pagination_link_count": preview.pagination_link_count,
            "pagination_fetch_count": preview.pagination_fetch_count,
            "detail_follow_enabled": preview.detail_follow_enabled,
            "detail_max_pages": preview.detail_max_pages,
            "detail_fetch_count": preview.detail_fetch_count,
            "detail_enriched_count": preview.detail_enriched_count,
            "request_notes": list(preview.request_notes),
            "warnings": list(preview.warnings),
            "run_steps": [
                {"phase": step.phase, "status": step.status, "detail": step.detail}
                for step in preview.run_steps
            ],
            "field_coverage": [
                {"field": field.field, "present_count": field.present_count, "total_count": field.total_count}
                for field in preview.field_coverage
            ],
            "field_checks": [
                {
                    "field": field.field,
                    "status": field.status,
                    "expected": field.expected,
                    "present_count": field.present_count,
                    "total_count": field.total_count,
                    "source": field.source,
                }
                for field in preview.field_checks
            ],
            "capability_checks": [
                {
                    "capability": check.capability,
                    "status": check.status,
                    "expected": check.expected,
                    "observed": check.observed,
                    "detail": check.detail,
                }
                for check in preview.capability_checks
            ],
            "detail_attempts": [
                {
                    "url": attempt.url,
                    "status": attempt.status,
                    "found_fields": attempt.found_fields,
                    "missing_fields": attempt.missing_fields,
                }
                for attempt in preview.detail_attempts
            ],
            "application_entries": [
                {"label": entry.label, "url": entry.url, "kind": entry.kind}
                for entry in preview.application_entries[:10]
            ],
            "jobs": [
                {"title": job.title, "url": job.url, "location": job.location}
                for job in preview.jobs[:10]
            ],
        }
    if source_review:
        state["source_review"] = {
            "source_id": source_review.get("source_id", ""),
            "status": source_review.get("status", ""),
            "readiness_status": source_review.get("readiness_status", ""),
            "job_count": source_review.get("job_count", 0),
            "warning_count": source_review.get("warning_count", 0),
            "worked": bool(source_review.get("worked")),
            "capability_count": len(source_review.get("capability_checks") or []),
            "pagination_fetch_count": source_review.get("pagination_fetch_count", 0),
            "detail_fetch_count": source_review.get("detail_fetch_count", 0),
        }
    return state


def _source_review_from_readiness(readiness, selected_source, *, root) -> dict | None:
    if not readiness or not getattr(readiness, "last_checked_at", ""):
        return None
    stale = bool(getattr(readiness, "checks", {}).get("recipe_changed_after_source_test"))
    status = str(getattr(readiness, "readiness_status", "") or "")
    insight = SourceWorkflowHandler(root).source_test_insight(selected_source, readiness=readiness)
    return {
        "source_id": selected_source.id,
        "source_name": selected_source.name,
        "status": str(getattr(readiness, "dry_run_status", "") or status),
        "readiness_status": status,
        "worked": status == "ready" and not stale,
        "stale": stale,
        "last_checked_at": getattr(readiness, "last_checked_at", ""),
        "summary": getattr(readiness, "readiness_summary", ""),
        "job_count": int(getattr(readiness, "dry_run_job_count", 0) or 0),
        "warning_count": int(getattr(readiness, "dry_run_warning_count", 0) or 0),
        "warnings": list(getattr(readiness, "dry_run_warnings", []) or []),
        "blockers": list(getattr(readiness, "blockers", []) or []),
        "sample_titles": list(getattr(readiness, "sample_titles", []) or []),
        "sample_urls": list(getattr(readiness, "sample_urls", []) or []),
        "capability_checks": list(getattr(readiness, "dry_run_capability_checks", []) or []),
        "pagination_fetch_count": _int_from_checks(readiness, "pagination_fetch_count"),
        "pagination_unique_jobs_from_fetched_pages": int(
            getattr(readiness, "dry_run_pagination_unique_jobs_from_fetched_pages", 0) or 0
        ),
        "pagination_duplicate_ratio": float(getattr(readiness, "dry_run_pagination_duplicate_ratio", 0.0) or 0.0),
        "detail_fetch_count": _int_from_checks(readiness, "detail_fetch_count"),
        "detail_verified_listing_page_count": _int_from_checks(readiness, "detail_verified_listing_page_count"),
        "run_steps": [],
        "jobs": [],
        "source_test_insight": insight,
        "source_test_url": f"/sources/{selected_source.id}/test-run",
        "run_source_test_url": f"/sources/{selected_source.id}/test-run?start=1",
        "review_source": "latest_source_test",
    }


def _source_review_from_payload(payload: dict, selected_source) -> dict:
    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    return {
        "source_id": selected_source.id,
        "source_name": selected_source.name,
        "status": str(payload.get("status") or ""),
        "readiness_status": str(payload.get("readiness_status") or readiness.get("status") or ""),
        "worked": str(payload.get("readiness_status") or readiness.get("status") or "") == "ready",
        "stale": False,
        "last_checked_at": str(readiness.get("last_checked_at") or ""),
        "summary": str(payload.get("readiness_summary") or readiness.get("summary") or ""),
        "job_count": int(payload.get("job_count") or 0),
        "warning_count": int(payload.get("warning_count") or 0),
        "warnings": list(payload.get("warnings") or []),
        "blockers": list(payload.get("readiness_blockers") or []),
        "sample_titles": [str(job.get("title") or "") for job in (payload.get("jobs") or [])[:5] if isinstance(job, dict)],
        "sample_urls": [str(job.get("url") or "") for job in (payload.get("jobs") or [])[:5] if isinstance(job, dict) and job.get("url")],
        "capability_checks": list(payload.get("capability_checks") or []),
        "pagination_fetch_count": int(payload.get("pagination_fetch_count") or 0),
        "pagination_unique_jobs_from_fetched_pages": int(payload.get("pagination_unique_jobs_from_fetched_pages") or 0),
        "pagination_duplicate_ratio": float(payload.get("pagination_duplicate_ratio") or 0.0),
        "detail_fetch_count": int(payload.get("detail_fetch_count") or 0),
        "detail_verified_listing_page_count": int(payload.get("detail_verified_listing_page_count") or 0),
        "run_steps": list(payload.get("run_steps") or []),
        "jobs": list(payload.get("jobs") or [])[:12],
        "source_test_insight": payload.get("source_test_insight") or {},
        "source_test_url": f"/sources/{selected_source.id}/test-run",
        "run_source_test_url": f"/sources/{selected_source.id}/test-run?start=1",
        "review_source": "fresh_source_test",
    }


def _int_from_checks(readiness, key: str) -> int:
    checks = getattr(readiness, "checks", {}) or {}
    try:
        value = int(checks.get(key) or 0)
    except (TypeError, ValueError):
        value = 0
    if value:
        return value
    return _int_from_capability_checks(list(getattr(readiness, "dry_run_capability_checks", []) or []), key)


def _int_from_capability_checks(capability_checks: list[dict], key: str) -> int:
    patterns = {
        "pagination_fetch_count": [
            r"proof fetched\s+(\d+)\s+page",
            r"Fetched\s+(\d+)\s+pagination page",
        ],
        "detail_fetch_count": [
            r"Attempted\s+(\d+)\s+detail page",
        ],
        "detail_verified_listing_page_count": [
            r"Verified details on\s+(\d+)\s*/\s*\d+\s+listing page",
            r"(\d+)\s+yielded configured detail fields",
        ],
    }.get(key, [])
    for check in capability_checks:
        detail = str(check.get("detail") or "")
        for pattern in patterns:
            match = re.search(pattern, detail, flags=re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except (TypeError, ValueError):
                    continue
    return 0
