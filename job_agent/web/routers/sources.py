from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from job_agent.web.dependencies import execution_source_service, source_registry_service, templates

router = APIRouter()


@router.get("/sources", response_class=HTMLResponse)
def source_overview(request: Request) -> HTMLResponse:
    sources = source_registry_service().list_sources()
    return templates.TemplateResponse(
        request,
        "sources.html",
        {"request": request, "sources": sources},
    )


@router.get("/sources/{source_id}", response_class=HTMLResponse)
def source_detail(request: Request, source_id: str, message: str = "", warning: str = "") -> HTMLResponse:
    source = source_registry_service().get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found.")
    execution_entry = execution_source_service().find_by_source_id(source.id)
    return templates.TemplateResponse(
        request,
        "source_detail.html",
        {
            "request": request,
            "source": source,
            "recipe_preview_url": _recipe_preview_url(source),
            "execution_entry": execution_entry,
            "execution_message": message,
            "execution_warning": warning,
        },
    )


@router.post("/sources/{source_id}/execution/create")
def create_execution_source(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_recipe_source(source)
    execution_source_service().create_or_update_recipe_source(source, enabled=False)
    return _redirect_to_source(source_id, message="Disabled execution entry created.")


@router.post("/sources/{source_id}/execution/update")
def update_execution_source(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_recipe_source(source)
    execution_source_service().create_or_update_recipe_source(source, enabled=False)
    return _redirect_to_source(source_id, message="Execution entry updated and kept disabled.")


@router.post("/sources/{source_id}/execution/enable")
def enable_execution_source(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_recipe_source(source)
    if source.health.health_status != "good":
        return _redirect_to_source(source_id, warning="Run recipe preview and save source health before enabling.")
    try:
        execution_source_service().enable(source.id)
    except KeyError:
        return _redirect_to_source(source_id, warning="Create a disabled execution entry before enabling.")
    return _redirect_to_source(source_id, message="Execution entry enabled for daily runs.")


@router.post("/sources/{source_id}/execution/disable")
def disable_execution_source(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    try:
        execution_source_service().disable(source.id)
    except KeyError:
        return _redirect_to_source(source_id, warning="No execution entry exists to disable.")
    return _redirect_to_source(source_id, message="Execution entry disabled.")


def _recipe_preview_url(source) -> str:
    if not source.recipe_path:
        return ""
    params = {
        "recipe_path": source.recipe_path,
        "input_path_or_url": source.url,
        "base_url": source.url,
        "mode": "default",
        "source_id": source.id,
    }
    return f"/recipe-preview?{urlencode(params)}"


def _registry_source_or_404(source_id: str):
    source = source_registry_service().get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found.")
    return source


def _require_recipe_source(source) -> None:
    if not source.recipe_path:
        raise HTTPException(status_code=400, detail="Only recipe-backed sources can be configured for recipe execution.")


def _redirect_to_source(source_id: str, *, message: str = "", warning: str = "") -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if warning:
        params["warning"] = warning
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/sources/{source_id}{suffix}", status_code=303)
