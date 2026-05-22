from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from job_agent.services.recipe_preview_service import explain_recipe, preview_recipe
from job_agent.services.source_health_service import SourceHealthService
from job_agent.web.debug_state import record_debug_event
from job_agent.web.dependencies import current_root, templates
from job_agent.web.form_options import default_recipe_for_source, recipe_options, source_options

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
) -> HTMLResponse:
    root = current_root()
    sources = source_options(root)
    recipes = recipe_options(root)
    source_key = selected_source_id.strip() or source_id.strip()
    selected_source = next((source for source in sources if source.id == source_key), None)
    recipe_path = recipe_path or default_recipe_for_source(selected_source, recipes)
    if selected_source and not input_path_or_url:
        input_path_or_url = selected_source.url
    saved_health = SourceHealthService(root).get_health(selected_source.id) if selected_source else None
    recipe_explanation = explain_recipe(recipe_path, root=root) if recipe_path else None
    normalized_source_mode = source_mode if source_mode in {"configured", "custom"} else "configured"
    normalized_tab = tab if tab in {"execute", "explain"} else "execute"
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
    return templates.TemplateResponse(
        request,
        "recipe_preview.html",
        {
            "request": request,
            "preview": None,
            "recipe_explanation": recipe_explanation,
            "recipe_path": recipe_path,
            "input_path_or_url": input_path_or_url,
            "source_id": source_id,
            "source_mode": normalized_source_mode,
            "selected_source_id": source_key,
            "sources": sources,
            "recipe_options": recipes,
            "tab": normalized_tab,
            "saved_health": saved_health,
        },
    )


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
    return templates.TemplateResponse(
        request,
        "recipe_preview.html",
        {
            "request": request,
            "preview": preview,
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
            "saved_health": saved_health,
        },
    )


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
    health_saved: bool = False,
    error: str = "",
) -> dict:
    state = {
        "source_mode": source_mode,
        "selected_source_id": selected_source.id if selected_source else selected_source_id,
        "selected_source": _source_debug_option(selected_source) if selected_source else None,
        "input_path_or_url": input_path_or_url,
        "recipe_path": recipe_path,
        "recipe_label": _recipe_label(recipe_path, recipes),
        "tab": tab,
        "health_saved": health_saved,
        "sources": _source_debug_options(sources),
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
    return state


def _source_debug_options(sources: list) -> list[dict[str, str]]:
    return [_source_debug_option(source) for source in sources]


def _source_debug_option(source) -> dict[str, str]:
    return {
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "recipe_path": source.recipe_path,
        "kind": source.kind,
        "status": source.status,
    }


def _recipe_label(recipe_path: str, recipes: list[dict[str, str]]) -> str:
    return next((recipe["label"] for recipe in recipes if recipe["value"] == recipe_path), "")
