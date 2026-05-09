from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from job_agent.web.dependencies import source_registry_service, templates

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
def source_detail(request: Request, source_id: str) -> HTMLResponse:
    source = source_registry_service().get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found.")
    return templates.TemplateResponse(
        request,
        "source_detail.html",
        {
            "request": request,
            "source": source,
            "recipe_preview_url": _recipe_preview_url(source),
        },
    )


def _recipe_preview_url(source) -> str:
    if not source.recipe_path:
        return ""
    params = {
        "recipe_path": source.recipe_path,
        "input_path_or_url": source.url,
        "base_url": source.url,
        "mode": "default",
    }
    return f"/recipe-preview?{urlencode(params)}"
