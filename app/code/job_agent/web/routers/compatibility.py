from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from job_agent.services.job_board_check_service import check_job_board_compatibility
from job_agent.web.debug_state import record_debug_event
from job_agent.web.dependencies import current_root, templates
from job_agent.web.form_options import (
    default_recipe_for_source,
    include_selected_recipe_option,
    recipe_options,
    source_options,
)
from job_agent.web.view_models.source_debug import recipe_label, source_debug_option, source_debug_options

router = APIRouter()


@router.get("/compatibility", response_class=HTMLResponse)
def compatibility_form(
    request: Request,
    url: str = Query(""),
    recipe_path: str = Query(""),
    source_mode: str = Query("configured"),
    selected_source_id: str = Query(""),
    show_saved: bool = Query(False),
) -> HTMLResponse:
    root = current_root()
    recipes = recipe_options(root)
    sources = source_options(root)
    selected_source = next((source for source in sources if source.id == selected_source_id.strip()), None)
    if selected_source:
        url = url or selected_source.url
        recipe_path = recipe_path or default_recipe_for_source(selected_source, recipes)
    recipes = include_selected_recipe_option(recipes, recipe_path)
    normalized_source_mode = source_mode if source_mode in {"configured", "custom"} else "configured"
    saved_health = selected_source.health if show_saved and selected_source else None
    record_debug_event(
        root,
        feature="compatibility",
        action="render_form",
        method=request.method,
        request_path=str(request.url),
        state={
            "source_mode": normalized_source_mode,
            "selected_source_id": selected_source.id if selected_source else selected_source_id,
            "url": url,
            "recipe_path": recipe_path,
            "source_count": len(sources),
            "recipe_count": len(recipes),
            "sources": source_debug_options(sources),
            "recipes": recipes,
        },
    )
    return templates.TemplateResponse(
        request,
        "compatibility.html",
        {
            "request": request,
            "title": f"Compatibility - {selected_source.name}" if selected_source else "Compatibility",
            "report": None,
            "sources": sources,
            "recipe_options": recipes,
            "source_mode": normalized_source_mode,
            "selected_source_id": selected_source.id if selected_source else selected_source_id,
            "url": url,
            "recipe_path": recipe_path,
            "saved_health": saved_health,
        },
    )


@router.post("/compatibility", response_class=HTMLResponse)
def run_compatibility_check(
    request: Request,
    url: str = Form(""),
    render: bool = Form(False),
    recipe_path: str = Form(""),
    source_mode: str = Form("configured"),
    selected_source_id: str = Form(""),
) -> HTMLResponse:
    root = current_root()
    sources = source_options(root)
    recipes = recipe_options(root)
    selected_source = None
    if source_mode == "configured" and selected_source_id.strip():
        selected_source = next((source for source in sources if source.id == selected_source_id.strip()), None)
        if selected_source:
            url = url or selected_source.url
            recipe_path = recipe_path or default_recipe_for_source(selected_source, recipes)
    recipes = include_selected_recipe_option(recipes, recipe_path)
    if not url.strip().startswith(("http://", "https://")):
        record_debug_event(
            root,
            feature="compatibility",
            action="validation_failed",
            method=request.method,
            request_path=str(request.url),
            state=_compatibility_debug_state(
                url=url,
                render=render,
                recipe_path=recipe_path,
                source_mode=source_mode,
                selected_source=selected_source,
                selected_source_id=selected_source_id,
                sources=sources,
                recipes=recipes,
                error="Enter a public http(s) source URL.",
            ),
        )
        raise HTTPException(status_code=400, detail="Enter a public http(s) source URL.")
    try:
        report = check_job_board_compatibility(
            url,
            render=render,
            recipe_path=recipe_path,
            root=root,
        )
    except ValueError as exc:
        record_debug_event(
            root,
            feature="compatibility",
            action="check_failed",
            method=request.method,
            request_path=str(request.url),
            state=_compatibility_debug_state(
                url=url,
                render=render,
                recipe_path=recipe_path,
                source_mode=source_mode,
                selected_source=selected_source,
                selected_source_id=selected_source_id,
                sources=sources,
                recipes=recipes,
                error=str(exc),
            ),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_debug_event(
        root,
        feature="compatibility",
        action="check_completed",
        method=request.method,
        request_path=str(request.url),
        state=_compatibility_debug_state(
            url=url,
            render=render,
            recipe_path=recipe_path,
            source_mode=source_mode,
            selected_source=selected_source,
            selected_source_id=selected_source_id,
            sources=sources,
            recipes=recipes,
            report=report,
        ),
    )
    return templates.TemplateResponse(
        request,
        "compatibility.html",
        {
            "request": request,
            "title": f"Compatibility - {selected_source.name}" if selected_source else "Compatibility",
            "report": report,
            "url": url,
            "render": render,
            "recipe_path": recipe_path,
            "source_mode": source_mode if source_mode in {"configured", "custom"} else "configured",
            "selected_source_id": selected_source.id if selected_source else selected_source_id,
            "sources": sources,
            "recipe_options": recipes,
            "saved_health": selected_source.health if selected_source else None,
        },
    )


def _compatibility_debug_state(
    *,
    url: str,
    render: bool,
    recipe_path: str,
    source_mode: str,
    selected_source,
    selected_source_id: str,
    sources: list,
    recipes: list[dict[str, str]],
    report=None,
    error: str = "",
) -> dict:
    state = {
        "source_mode": source_mode,
        "selected_source_id": selected_source.id if selected_source else selected_source_id,
        "selected_source": source_debug_option(selected_source) if selected_source else None,
        "url": url,
        "render": render,
        "recipe_path": recipe_path,
        "recipe_label": recipe_label(recipe_path, recipes),
        "sources": source_debug_options(sources),
        "recipes": recipes,
        "error": error,
    }
    if report:
        state["report"] = {
            "recommendation": report.recommendation,
            "recommendation_reason": report.recommendation_reason,
            "input_type": report.input_type,
            "normal_html": _quality_summary(report.normal_html),
            "rendered_page": _quality_summary(report.rendered_page),
            "findings": [
                {"label": finding.label, "status": finding.status, "detail": finding.detail}
                for finding in report.findings
            ],
        }
    return state


def _quality_summary(quality) -> dict | None:
    if not quality:
        return None
    return {
        "label": quality.label,
        "candidate_count": quality.candidate_count,
        "useful_title_count": quality.useful_title_count,
        "generic_title_count": quality.generic_title_count,
        "unique_url_count": quality.unique_url_count,
        "average_description_length": quality.average_description_length,
        "warnings": list(quality.warnings),
    }
