from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from job_agent.services.recipe_preview_service import preview_recipe
from job_agent.services.source_health_service import SourceHealthService
from job_agent.web.dependencies import current_root, templates

router = APIRouter()


@router.get("/recipe-preview", response_class=HTMLResponse)
def recipe_preview_form(
    request: Request,
    recipe_path: str = Query(""),
    input_path_or_url: str = Query(""),
    base_url: str = Query(""),
    mode: str = Query("default"),
    source_id: str = Query(""),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "recipe_preview.html",
        {
            "request": request,
            "preview": None,
            "recipe_path": recipe_path,
            "input_path_or_url": input_path_or_url,
            "base_url": base_url,
            "mode": mode if mode in {"default", "static", "rendered"} else "default",
            "source_id": source_id,
        },
    )


@router.post("/recipe-preview", response_class=HTMLResponse)
def run_recipe_preview(
    request: Request,
    recipe_path: str = Form(""),
    input_path_or_url: str = Form(""),
    base_url: str = Form(""),
    mode: str = Form("default"),
    source_id: str = Form(""),
) -> HTMLResponse:
    rendered = mode == "rendered"
    static = mode == "static"
    try:
        if mode not in {"default", "static", "rendered"}:
            raise ValueError("Mode must be default, static, or rendered.")
        preview = preview_recipe(
            recipe_path,
            input_path_or_url,
            base_url=base_url,
            rendered=rendered,
            static=static,
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
                "rendered_html" if rendered else "static_html" if static else "unknown",
                str(exc),
            )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "recipe_preview.html",
        {
            "request": request,
            "preview": preview,
            "recipe_path": recipe_path,
            "input_path_or_url": input_path_or_url,
            "base_url": base_url,
            "mode": mode,
            "source_id": source_id,
            "health_saved": health_saved,
        },
    )
