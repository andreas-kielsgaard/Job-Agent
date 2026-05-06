from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from job_agent.config import ROOT
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.setup_service import SetupService
from job_agent.web.dependencies import templates
from job_agent.web.view_models.setup import build_setup_view

router = APIRouter()


@router.get("/setup", response_class=HTMLResponse)
def setup(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "setup.html", {"request": request, **build_setup_view()})


@router.post("/setup/env")
def save_env(
    anthropic_api_key: str = Form(""),
    claude_model: str = Form("claude-sonnet-4-0"),
    claude_use_by_default: bool = Form(False),
) -> RedirectResponse:
    SetupService(ROOT).save_env_settings(anthropic_api_key, claude_model, claude_use_by_default)
    return RedirectResponse(url="/setup", status_code=303)


@router.post("/setup/contact")
def save_contact(
    name: str = Form(""),
    title: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    linkedin: str = Form(""),
    location: str = Form(""),
    address: str = Form(""),
    post_code: str = Form(""),
    city: str = Form(""),
    country: str = Form(""),
    kommune: str = Form(""),
) -> RedirectResponse:
    SetupService(ROOT).save_contact(
        {
            "name": name,
            "title": title,
            "phone": phone,
            "email": email,
            "linkedin": linkedin,
            "location": location,
            "address": address,
            "post_code": post_code,
            "city": city,
            "country": country,
            "kommune": kommune,
        }
    )
    return RedirectResponse(url="/setup#profile", status_code=303)


@router.post("/setup/preferences")
def save_preferences(
    available_from: str = Form(""),
    logistics: str = Form(""),
    current_base: str = Form(""),
    onsite_roles: str = Form(""),
    preferred_regions: str = Form(""),
    interests: str = Form(""),
    minimum_digest_score: int = Form(45),
) -> RedirectResponse:
    SetupService(ROOT).save_preferences(
        available_from=available_from,
        logistics=logistics,
        current_base=current_base,
        onsite_roles=onsite_roles,
        preferred_regions=preferred_regions,
        interests=interests,
        minimum_digest_score=minimum_digest_score,
    )
    return RedirectResponse(url="/setup#profile", status_code=303)


@router.post("/setup/cv-reference")
async def upload_cv_reference(cv_file: UploadFile = File(...), extract_to_canonical: bool = Form(False)) -> RedirectResponse:
    try:
        await _store_uploaded_cv(cv_file, extract_to_canonical)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/setup#cv-reference", status_code=303)


async def _store_uploaded_cv(cv_file: UploadFile, extract_to_canonical: bool) -> None:
    if not cv_file.filename:
        return
    CvReferenceService(ROOT).store_reference_cv(cv_file.filename, await cv_file.read(), extract_to_canonical)


@router.post("/setup/source-toggle")
def toggle_source(index: int = Form(...), enabled: bool = Form(False)) -> RedirectResponse:
    try:
        SetupService(ROOT).toggle_source(index, enabled)
    except IndexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/setup#sources", status_code=303)


@router.post("/setup/source-add")
def add_source(
    name: str = Form(...),
    url: str = Form(""),
    source_type: str = Form("generic_html"),
    keywords: str = Form(""),
    enabled: bool = Form(True),
) -> RedirectResponse:
    SetupService(ROOT).add_source(name=name, url=url, source_type=source_type, keywords=keywords, enabled=enabled)
    return RedirectResponse(url="/setup#sources", status_code=303)


@router.post("/setup/file")
def save_setup_file(file_key: str = Form(...), content: str = Form(...)) -> RedirectResponse:
    try:
        SetupService(ROOT).save_setup_file(file_key, content)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/setup", status_code=303)
