from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from job_agent.llm import DEFAULT_CLAUDE_MODEL
from job_agent.web.dependencies import templates, workflow_handler
from job_agent.web.runtime import runtime

router = APIRouter()


@router.get("/setup", response_class=HTMLResponse)
def setup(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "setup.html", {"request": request, **workflow_handler().profile.setup_view()})


@router.post("/setup/env")
def save_env(
    anthropic_api_key: str = Form(""),
    claude_model: str = Form(DEFAULT_CLAUDE_MODEL),
    claude_use_by_default: bool = Form(False),
) -> RedirectResponse:
    workflow_handler().profile.save_env_settings(anthropic_api_key, claude_model, claude_use_by_default)
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
    workflow_handler().profile.save_contact(
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
) -> RedirectResponse:
    workflow_handler().profile.save_preferences(
        available_from=available_from,
        logistics=logistics,
        current_base=current_base,
        onsite_roles=onsite_roles,
        preferred_regions=preferred_regions,
        interests=interests,
    )
    return RedirectResponse(url="/setup#preferences", status_code=303)


@router.post("/setup/run-inclusion")
def save_run_inclusion(minimum_digest_score: int = Form(45)) -> RedirectResponse:
    workflow_handler().profile.save_run_inclusion(minimum_digest_score)
    return RedirectResponse(url="/setup#match-engine", status_code=303)


@router.post("/setup/runtime")
def save_runtime_settings(max_parallel_sources: int = Form(10)) -> RedirectResponse:
    workflow_handler().profile.save_runtime_settings(max_parallel_sources)
    return RedirectResponse(url="/setup#match-engine", status_code=303)


@router.post("/setup/match-engine")
async def save_match_engine(request: Request) -> RedirectResponse:
    workflow_handler().profile.save_match_engine_settings_from_form(await request.form())
    return RedirectResponse(url="/setup#match-engine", status_code=303)


@router.post("/setup/skills")
async def save_skills(request: Request) -> RedirectResponse:
    workflow_handler().profile.save_skill_matrix_from_form(await request.form())
    return RedirectResponse(url="/setup#skill-matrix", status_code=303)


@router.post("/setup/case-studies")
async def save_case_studies(request: Request) -> RedirectResponse:
    workflow_handler().profile.save_case_studies_from_form(await request.form())
    return RedirectResponse(url="/setup#case-studies", status_code=303)


@router.post("/setup/writing-reference")
def save_writing_reference(
    canonical_cv: str | None = Form(None),
    writing_style: str | None = Form(None),
) -> RedirectResponse:
    workflow_handler().profile.save_writing_reference(canonical_cv=canonical_cv, writing_style=writing_style)
    return RedirectResponse(url="/setup#writing-reference", status_code=303)


@router.post("/setup/application-examples")
async def save_application_examples(request: Request) -> RedirectResponse:
    workflow_handler().profile.save_application_examples_from_form(await request.form())
    return RedirectResponse(url="/setup#writing-reference", status_code=303)


@router.post("/setup/ai-policy")
async def save_ai_policy(request: Request) -> RedirectResponse:
    workflow_handler().profile.save_ai_policy_from_form(await request.form())
    return RedirectResponse(url="/setup#ai-writing", status_code=303)


@router.post("/setup/cv-reference", response_model=None)
async def upload_cv_reference(
    request: Request,
    cv_file: UploadFile = File(...),
    extract_to_canonical: bool = Form(False),
    auto_configure_profile: bool = Form(False),
    preview_profile_configuration: bool = Form(False),
    configure_canonical_cv: bool = Form(False),
    configure_skills: bool = Form(False),
    configure_experience: bool = Form(False),
    configure_preferences: bool = Form(False),
    configure_match_engine: bool = Form(False),
    work_task_id: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    task_id = ""
    if auto_configure_profile:
        task_id = _start_profile_draft_task(work_task_id, "Drafting profile from uploaded CV")
        _update_profile_draft_task(task_id, "Extracting CV", "Uploading and extracting text from the CV.", 12)
    try:
        extracted_text = await _store_uploaded_cv(cv_file, extract_to_canonical)
    except ValueError as exc:
        if task_id:
            runtime.finish_profile_draft_task(task_id, status="failed", message=str(exc), error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if auto_configure_profile:
        form = {
            "configure_canonical_cv": configure_canonical_cv,
            "configure_skills": configure_skills,
            "configure_experience": configure_experience,
            "configure_preferences": configure_preferences,
            "configure_match_engine": configure_match_engine,
        }
        if preview_profile_configuration:
            return await _preview_auto_configure_from_cv_text(
                request,
                extracted_text,
                form,
                task_id,
                source_label="uploaded CV",
            )
        return await _auto_configure_from_cv_text(extracted_text, form, task_id)
    return _setup_redirect(message="CV reference uploaded.", anchor="cv-reference")


@router.post("/setup/cv-reference/configure", response_model=None)
async def configure_from_existing_cv(request: Request) -> HTMLResponse | RedirectResponse:
    reference = workflow_handler().profile.cv_reference()
    form = await request.form()
    task_id = _start_profile_draft_task(str(form.get("work_task_id") or ""), "Drafting profile from CV")
    _update_profile_draft_task(task_id, "Reading CV", "Using the extracted text from the uploaded CV.", 12)
    if _truthy(form.get("preview_profile_configuration")):
        return await _preview_auto_configure_from_cv_text(
            request,
            reference.get("extracted_text", ""),
            form,
            task_id,
            source_label="current reference CV",
        )
    return await _auto_configure_from_cv_text(reference.get("extracted_text", ""), form, task_id)


@router.post("/setup/cv-reference/apply-draft")
async def apply_cv_profile_draft(request: Request) -> RedirectResponse:
    form = await request.form()
    profile = workflow_handler().profile
    targets = profile.auto_config_targets_from_form(form)
    if not targets:
        return _setup_redirect(warning="Select at least one profile section to apply.", anchor="cv-profile-draft")
    try:
        draft_id = str(form.get("draft_id") or "")
        active_draft = profile.active_draft()
        if draft_id:
            if not active_draft or active_draft.get("id") != draft_id:
                return _setup_redirect(warning="That CV draft is no longer available.", anchor="cv-reference")
            data = active_draft.get("data") or {}
        else:
            data = json.loads(str(form.get("draft_json", "") or "{}"))
        result = profile.apply_profile_auto_configuration(data, targets)
    except (json.JSONDecodeError, ValueError) as exc:
        return _setup_redirect(warning=f"Could not apply CV draft: {exc}", anchor="cv-reference")
    if str(form.get("draft_id") or ""):
        profile.clear_profile_draft_task()
        profile.clear_active_draft(str(form.get("draft_id") or ""))
    applied = ", ".join(result["applied"]) or "no sections"
    missing = "; missing AI output for " + ", ".join(result["missing"]) if result["missing"] else ""
    return _setup_redirect(message=f"Applied CV draft: {applied}.{missing}", anchor="profile-contract")


@router.post("/setup/cv-reference/discard-draft")
async def discard_cv_profile_draft(request: Request) -> RedirectResponse:
    form = await request.form()
    profile = workflow_handler().profile
    profile.clear_profile_draft_task()
    cleared = profile.clear_active_draft(str(form.get("draft_id") or ""))
    if not cleared:
        return _setup_redirect(warning="That CV draft is no longer available.", anchor="cv-reference")
    return _setup_redirect(message="Discarded CV profile draft.", anchor="cv-reference")


async def _store_uploaded_cv(cv_file: UploadFile, extract_to_canonical: bool) -> str:
    if not cv_file.filename:
        return ""
    return workflow_handler().profile.store_reference_cv(
        cv_file.filename,
        await cv_file.read(),
        extract_to_canonical,
    )


async def _auto_configure_from_cv_text(cv_text: str, form, task_id: str = "") -> RedirectResponse:
    try:
        result = await run_in_threadpool(
            workflow_handler().profile.auto_configure_profile_from_cv,
            cv_text,
            workflow_handler().profile.auto_config_targets_from_form(form),
            progress_callback=_profile_draft_progress_callback(task_id),
        )
    except (RuntimeError, ValueError) as exc:
        if task_id:
            runtime.finish_profile_draft_task(task_id, status="failed", message=str(exc), error_message=str(exc))
        return _setup_redirect(warning=str(exc), anchor="cv-reference")
    applied = ", ".join(result["applied"]) or "no sections"
    missing = "; missing AI output for " + ", ".join(result["missing"]) if result["missing"] else ""
    if task_id:
        runtime.finish_profile_draft_task(task_id, message=f"Applied CV draft: {applied}.{missing}")
    return _setup_redirect(message=f"Configured from CV: {applied}.{missing}", anchor="cv-reference")


async def _preview_auto_configure_from_cv_text(
    request: Request,
    cv_text: str,
    form,
    task_id: str = "",
    source_label: str = "CV",
) -> HTMLResponse | RedirectResponse:
    try:
        draft = await run_in_threadpool(
            workflow_handler().profile.draft_profile_auto_configuration_from_cv,
            cv_text,
            workflow_handler().profile.auto_config_targets_from_form(form),
            progress_callback=_profile_draft_progress_callback(task_id),
        )
    except (RuntimeError, ValueError) as exc:
        if task_id:
            runtime.finish_profile_draft_task(task_id, status="failed", message=str(exc), error_message=str(exc))
        return _setup_redirect(warning=str(exc), anchor="cv-reference")
    draft_record = workflow_handler().profile.save_draft(draft, source_label=source_label, task_id=task_id)
    if task_id:
        runtime.finish_profile_draft_task(
            task_id,
            message="Profile draft preview is ready for review.",
            draft_id=str(draft_record.get("id") or ""),
            href="/setup#cv-profile-draft",
        )
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "request": request,
            **workflow_handler().profile.setup_view(),
        },
    )


def _setup_redirect(message: str = "", warning: str = "", anchor: str = "") -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if warning:
        params["warning"] = warning
    query = f"?{urlencode(params)}" if params else ""
    fragment = f"#{anchor}" if anchor else ""
    return RedirectResponse(url=f"/setup{query}{fragment}", status_code=303)


def _truthy(value) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


def _start_profile_draft_task(task_id: str, title: str) -> str:
    task = runtime.start_profile_draft_task(task_id, title)
    return task.task_id


def _update_profile_draft_task(task_id: str, stage: str, message: str, progress_percent: int) -> None:
    if task_id:
        runtime.update_profile_draft_task(
            task_id,
            status="running",
            stage=stage,
            message=message,
            progress_percent=progress_percent,
        )


def _profile_draft_progress_callback(task_id: str):
    if not task_id:
        return None

    def progress(stage: str, message: str, progress_percent: int) -> None:
        _update_profile_draft_task(task_id, stage, message, progress_percent)

    return progress


@router.post("/setup/source-toggle")
def toggle_source(index: int = Form(...), enabled: bool = Form(False)) -> RedirectResponse:
    return RedirectResponse(
        url="/sources?warning=Source execution is managed from each source detail page.",
        status_code=303,
    )


@router.post("/setup/source-add")
def add_source(
    name: str = Form(...),
    url_or_path: str = Form(""),
    source_type: str = Form("generic_html"),
    keywords: str = Form(""),
    enabled: bool = Form(True),
) -> RedirectResponse:
    return RedirectResponse(
        url="/sources/new?warning=Use the source workflow to add and review job boards before daily-run use.",
        status_code=303,
    )


@router.post("/setup/file")
def save_setup_file(file_key: str = Form(...), content: str = Form(...)) -> RedirectResponse:
    try:
        workflow_handler().profile.save_setup_file(file_key, content)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/setup", status_code=303)
