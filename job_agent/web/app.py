from __future__ import annotations

import html
import hashlib
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from dotenv import dotenv_values
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from job_agent.application_status_store import APPLICATION_STATUSES, ApplicationStatusStore
from job_agent.config import ROOT, load_profile
from job_agent.digest import write_job_package
from job_agent.generator import generate_materials
from job_agent.models import Job, MatchResult
from job_agent.prompt_context import EditContextPreference, EditContextPreferenceStore, PromptContextProvider, run_ai_edit
from job_agent.run_service import run_daily_agent
from job_agent.run_store import RunOptions, RunStore
from job_agent.token_usage import TokenUsageStore


WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")
app = FastAPI(title="Job Agent", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
executor = ThreadPoolExecutor(max_workers=1)
last_request_at = time.time()
idle_monitor_started = False
APP_VERSION = ""

CLAUDE_MODELS = [
    {
        "label": "Balanced default",
        "value": "claude-sonnet-4-0",
        "quality": "High performance",
        "speed": "Medium",
        "price": "Medium",
        "help": "Tracks the current Sonnet 4 snapshot. Good default balance of quality and cost.",
    },
    {
        "label": "Stable balanced",
        "value": "claude-sonnet-4-20250514",
        "quality": "High performance",
        "speed": "Medium",
        "price": "Medium",
        "help": "Stable snapshot. Best if you want reproducible behavior.",
    },
    {
        "label": "Highest performance",
        "value": "claude-opus-4-1-20250805",
        "quality": "Highest performance",
        "speed": "Low",
        "price": "High",
        "help": "Most capable listed model, usually higher cost and slower.",
    },
    {
        "label": "Cheapest and fastest",
        "value": "claude-3-5-haiku-20241022",
        "quality": "Basic performance",
        "speed": "High",
        "price": "Low",
        "help": "Fast and cheaper, but weaker for nuanced writing.",
    },
]

TEMPLATE_VARIABLES = {
    "job": "The parsed job object, e.g. job.title, job.company, job.location, job.application_url.",
    "match": "Internal match result, e.g. match.reasons, match.concerns, match.recommended_angle. Avoid scores in recruiter-facing templates.",
    "contact": "Your profile contact fields, e.g. contact.name, contact.email, contact.linkedin.",
    "availability": "Availability fields from profile/preferences.yaml.",
    "location_policy": "Relocation and preferred-location fields from profile/preferences.yaml.",
    "top_skills": "Exactly five selected skills for the role.",
    "selected_experience": "The selected relevant experience entries.",
    "keyword_line": "Additional SAP keywords selected for this role.",
    "application_text": "Only available in form-answers template.",
}


def create_app() -> FastAPI:
    return app


@app.middleware("http")
async def track_activity(request: Request, call_next):
    global last_request_at
    last_request_at = time.time()
    return await call_next(request)


@app.on_event("startup")
def start_idle_monitor() -> None:
    global APP_VERSION
    APP_VERSION = compute_app_version(ROOT)
    RunStore(ROOT).recover_stale_runs()
    global idle_monitor_started
    if idle_monitor_started:
        return
    idle_monitor_started = True
    seconds = int(os.getenv("JOB_AGENT_IDLE_SHUTDOWN_SECONDS", "0") or "0")
    if seconds <= 0:
        return
    thread = threading.Thread(target=_idle_shutdown_loop, args=(seconds,), daemon=True)
    thread.start()


@app.get("/api/health")
def health() -> JSONResponse:
    active_run = next((run for run in RunStore(ROOT).list_runs() if run.status in {"pending", "running"}), None)
    return JSONResponse(
        {
            "status": "ok",
            "time": datetime.now(timezone.utc).isoformat(),
            "app_version": APP_VERSION or compute_app_version(ROOT),
            "active_run_id": active_run.run_id if active_run else "",
            "active_run_status": active_run.status if active_run else "",
        }
    )


def _idle_shutdown_loop(seconds: int) -> None:
    while True:
        time.sleep(10)
        if time.time() - last_request_at < seconds:
            continue
        if _has_active_run():
            continue
        os._exit(0)


def _has_active_run() -> bool:
    try:
        return any(run.status in {"pending", "running"} for run in RunStore(ROOT).list_runs())
    except Exception:
        return True


def compute_app_version(root: Path) -> str:
    hasher = hashlib.sha256()
    patterns = [
        "job_agent/**/*.py",
        "job_agent/web/templates/**/*.html",
        "job_agent/web/static/**/*",
        "templates/**/*.j2",
        "prompts/**/*.md",
        "requirements.txt",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in root.glob(pattern) if path.is_file())
    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        hasher.update(relative.encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:16]


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    store = RunStore(ROOT)
    runs = store.list_runs(include_tests=False)
    active_run = next((run for run in runs if run.status in {"pending", "running"}), None)
    latest_run = runs[0] if runs else None
    dashboard_stats = build_dashboard_stats(runs)
    default_options = latest_run.options if latest_run else {
        "use_llm": True,
        "include_seen": False,
        "include_weak": False,
        "mark_seen": True,
        "generate_materials": True,
    }
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "runs": runs[:8],
            "active_run": active_run,
            "latest_run": latest_run,
            "dashboard_stats": dashboard_stats,
            "default_options": default_options,
            "env": dotenv_values(ROOT / ".env"),
        },
    )


@app.post("/api/run")
def launch_run(
    use_llm: bool = Form(False),
    include_seen: bool = Form(False),
    include_weak: bool = Form(False),
    mark_seen: bool = Form(False),
    generate_materials_option: bool = Form(True),
    is_test: bool = Form(False),
) -> RedirectResponse:
    options = RunOptions(use_llm=use_llm, include_seen=include_seen, include_weak=include_weak, mark_seen=mark_seen, generate_materials=generate_materials_option, is_test=is_test)
    store = RunStore(ROOT)
    record = store.create_run(options)
    executor.submit(run_daily_agent, options, None, ROOT, record.run_id)
    return RedirectResponse(url=f"/runs/{record.run_id}", status_code=303)


@app.get("/api/runs/{run_id}/status")
def run_status(run_id: str) -> JSONResponse:
    store = RunStore(ROOT)
    record = store.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    events = store.read_events(run_id, limit=20)
    latest = events[-1] if events else {}
    return JSONResponse({"run": record.__dict__, "latest_event": latest, "recent_events": events})


@app.get("/runs", response_class=HTMLResponse)
def run_list(request: Request, view: str = "active") -> HTMLResponse:
    store = RunStore(ROOT)
    if view == "test":
        runs = [run for run in store.list_runs(include_archived=True, include_deleted=False, include_tests=True) if run.is_test and run.visibility == "active"]
    elif view == "archived":
        runs = store.list_runs(include_archived=True, include_deleted=False, include_tests=True)
        runs = [run for run in runs if run.visibility == "archived"]
    elif view == "deleted":
        runs = store.list_runs(include_archived=True, include_deleted=True, include_tests=True)
        runs = [run for run in runs if run.visibility == "deleted"]
    else:
        runs = store.list_runs(include_tests=False)
    return templates.TemplateResponse(request, "runs.html", {"request": request, "runs": runs, "view": view})


@app.post("/api/runs/bulk")
def bulk_runs(run_ids: list[str] = Form(...), action: str = Form(...), return_to: str = Form("/runs")) -> RedirectResponse:
    store = RunStore(ROOT)
    for run_id in run_ids:
        try:
            if action == "archive":
                store.archive(run_id)
            elif action == "delete":
                store.soft_delete(run_id)
            elif action == "restore":
                store.restore(run_id)
        except KeyError:
            continue
    return RedirectResponse(url=return_to, status_code=303)


@app.post("/api/runs/{run_id}/archive")
def archive_run(run_id: str, return_to: str = Form("/runs")) -> RedirectResponse:
    RunStore(ROOT).archive(run_id)
    return RedirectResponse(url=return_to, status_code=303)


@app.post("/api/runs/{run_id}/delete")
def delete_run(run_id: str, return_to: str = Form("/runs")) -> RedirectResponse:
    RunStore(ROOT).soft_delete(run_id)
    return RedirectResponse(url=return_to, status_code=303)


@app.post("/api/runs/{run_id}/restore")
def restore_run(run_id: str, return_to: str = Form("/runs")) -> RedirectResponse:
    RunStore(ROOT).restore(run_id)
    return RedirectResponse(url=return_to, status_code=303)


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request) -> HTMLResponse:
    runs = RunStore(ROOT).list_runs(include_tests=False)
    packages = list_unique_jobs()
    statuses = ApplicationStatusStore(ROOT).list_all()
    status_counts = {}
    for record in statuses:
        status_counts[record.status] = status_counts.get(record.status, 0) + 1
    stats = {
        "total_runs": len(runs),
        "completed_runs": sum(1 for run in runs if run.status == "completed"),
        "total_loaded": sum(run.total_loaded for run in runs),
        "total_generated": sum(run.generated_job_count for run in runs),
        "unique_jobs": len(packages),
        "strong_jobs": sum(1 for job in packages if job.get("match_category") == "strong"),
        "exploratory_jobs": sum(1 for job in packages if job.get("match_category") == "exploratory"),
        "applied_total": status_counts.get("applied", 0),
        "interesting_total": status_counts.get("interesting", 0),
        "not_interesting_total": status_counts.get("not_interesting", 0),
        "avg_score": round(sum(job.get("match_score", 0) for job in packages) / len(packages), 1) if packages else 0,
    }
    return templates.TemplateResponse(request, "stats.html", {"request": request, "stats": stats, "status_counts": status_counts, "runs": runs[:10]})


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request) -> HTMLResponse:
    app_statuses = request.query_params.getlist("app_status")
    categories = request.query_params.getlist("category")
    jobs = list_unique_jobs()
    if app_statuses:
        jobs = [job for job in jobs if job.get("application_status") in app_statuses]
    if categories:
        jobs = [job for job in jobs if job.get("match_category") in categories]
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"request": request, "jobs": jobs, "filters": {"app_statuses": app_statuses, "categories": categories}},
    )


@app.post("/api/jobs/bulk-status")
def bulk_job_status(job_ids: list[str] = Form(...), status: str = Form(...), return_to: str = Form("/jobs")) -> RedirectResponse:
    store = ApplicationStatusStore(ROOT)
    for job_id in job_ids:
        try:
            store.update_status(job_id, status)
            refresh_package_status(job_id, status)
        except KeyError:
            continue
    return RedirectResponse(url=return_to, status_code=303)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(
    request: Request,
    run_id: str,
    category: str = "",
    app_status: str = "",
    source: str = "",
) -> HTMLResponse:
    store = RunStore(ROOT)
    record = store.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    packages = list_packages(run_id)
    if category:
        packages = [pkg for pkg in packages if pkg.get("match_category") == category]
    if app_status:
        packages = [pkg for pkg in packages if pkg.get("application_status") == app_status]
    if source:
        packages = [pkg for pkg in packages if source.lower() in str(pkg.get("source_url", "")).lower()]
    token_records = TokenUsageStore(ROOT).list_for_run(run_id)
    source_warnings = [event for event in store.read_events(run_id) if event.get("event_type") == "source_warning"]
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "request": request,
            "run": record,
            "packages": packages,
            "events": store.read_events(run_id, limit=12),
            "source_warnings": source_warnings,
            "token_records": token_records,
            "filters": {"category": category, "app_status": app_status, "source": source},
        },
    )


@app.get("/runs/{run_id}/log", response_class=HTMLResponse)
def run_log(request: Request, run_id: str) -> HTMLResponse:
    record = RunStore(ROOT).get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    log_text = Path(record.run_log_path).read_text(encoding="utf-8") if record.run_log_path and Path(record.run_log_path).exists() else ""
    return templates.TemplateResponse(request, "log.html", {"request": request, "run": record, "log_text": log_text})


@app.get("/api/runs/{run_id}/log")
def run_log_text(run_id: str) -> PlainTextResponse:
    record = RunStore(ROOT).get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    path = Path(record.run_log_path)
    return PlainTextResponse(path.read_text(encoding="utf-8") if path.exists() else "")


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str, run_id: str = "") -> HTMLResponse:
    package = find_package(job_id, run_id)
    if not package:
        raise HTTPException(status_code=404, detail="Job package not found")
    files = read_package_files(package)
    status = ApplicationStatusStore(ROOT).get(job_id)
    cv_reference = get_cv_reference()
    review_bundle = build_review_bundle(package, files, status)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "request": request,
            "package": package,
            "files": files,
            "status": status,
            "statuses": sorted(APPLICATION_STATUSES),
            "render_md": markdown_to_html,
            "cv_reference": cv_reference,
            "review_bundle": review_bundle,
        },
    )


@app.post("/api/jobs/{job_id}/status")
def update_job_status(
    job_id: str,
    status: str = Form(...),
    notes: str = Form(""),
    not_interesting_reason: str = Form(""),
    return_to: str = Form(""),
) -> RedirectResponse:
    ApplicationStatusStore(ROOT).update_status(job_id, status, notes=notes, not_interesting_reason=not_interesting_reason)
    refresh_package_status(job_id, status)
    return RedirectResponse(url=return_to or f"/jobs/{job_id}", status_code=303)


@app.get("/api/ai-edit/context")
def ai_edit_context(field_id: str, button_id: str, job_id: str = "", run_id: str = "") -> JSONResponse:
    provider = PromptContextProvider(ROOT)
    package = find_package(job_id, run_id) if job_id else None
    files = read_package_files(package) if package else {}
    blocks = provider.available_blocks(package, files)
    defaults = provider.default_blocks_for_field(field_id)
    pref = EditContextPreferenceStore(ROOT).get(button_id, defaults)
    return JSONResponse(
        {
            "field_id": field_id,
            "button_id": button_id,
            "field_context": provider_context_for(field_id),
            "blocks": [block.__dict__ for block in blocks.values()],
            "selected_blocks": pref.selected_blocks,
            "disabled_blocks": pref.disabled_blocks,
        }
    )


@app.post("/api/ai-edit/generate")
async def ai_edit_generate(request: Request) -> JSONResponse:
    data = await request.json()
    field_id = data.get("field_id", "")
    button_id = data.get("button_id", field_id)
    current_text = data.get("current_text", "")
    user_instruction = data.get("user_instruction", "")
    selected_blocks = data.get("selected_blocks", [])
    disabled_blocks = data.get("disabled_blocks", [])
    job_id = data.get("job_id", "")
    run_id = data.get("run_id", "")

    provider = PromptContextProvider(ROOT)
    package = find_package(job_id, run_id) if job_id else None
    files = read_package_files(package) if package else {}
    prompt = provider.build_prompt(
        field_id=field_id,
        current_text=current_text,
        user_instruction=user_instruction,
        selected_blocks=selected_blocks,
        disabled_blocks=disabled_blocks,
        job_package=package,
        job_files=files,
    )
    EditContextPreferenceStore(ROOT).save(EditContextPreference(button_id=button_id, selected_blocks=selected_blocks, disabled_blocks=disabled_blocks))
    try:
        revised, model = run_ai_edit(prompt, ROOT)
        return JSONResponse({"ok": True, "revised_text": revised, "prompt": prompt, "model": model})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), "prompt": prompt}, status_code=400)


@app.post("/api/jobs/{job_id}/materials")
def save_job_materials(
    job_id: str,
    cv: str = Form(""),
    application: str = Form(""),
    form_answers: str = Form(""),
    match_analysis: str = Form(""),
    return_to: str = Form(""),
) -> RedirectResponse:
    package = find_package(job_id)
    if not package:
        raise HTTPException(status_code=404, detail="Job package not found")
    updates = {"cv": cv, "application": application, "form_answers": form_answers, "match_analysis": match_analysis}
    for key, content in updates.items():
        path_text = package.get("paths", {}).get(key)
        if path_text:
            Path(path_text).write_text(content, encoding="utf-8")
    mark_package_materials_generated(package, True)
    return RedirectResponse(url=return_to or f"/jobs/{job_id}", status_code=303)


@app.post("/api/jobs/{job_id}/generate")
def generate_job_materials(
    job_id: str,
    use_llm: bool = Form(False),
    return_to: str = Form(""),
) -> RedirectResponse:
    package = find_package(job_id)
    if not package:
        raise HTTPException(status_code=404, detail="Job package not found")
    files = read_package_files(package)
    if not files.get("job") or not files.get("match"):
        raise HTTPException(status_code=400, detail="Job or match JSON missing")
    job = Job.from_mapping(json.loads(files["job"]))
    match_data = json.loads(files["match"])
    match = MatchResult(**match_data)
    profile = load_profile(ROOT)
    generated = generate_materials(
        job,
        match,
        profile,
        use_llm=use_llm,
        root=ROOT,
        run_id=package.get("run_id", ""),
        stable_id=package.get("stable_id", ""),
    )
    paths = write_job_package(
        job,
        match,
        generated,
        infer_package_date(package),
        root=ROOT,
        run_id=package.get("run_id", ""),
        stable_id=package.get("stable_id", ""),
        fuzzy_key=package.get("fuzzy_key", ""),
        state=package.get("state", ""),
        application_status=package.get("application_status", "unreviewed"),
    )
    refreshed = json.loads(Path(paths["index"]).read_text(encoding="utf-8"))
    refreshed["materials_generated"] = True
    Path(paths["index"]).write_text(json.dumps(refreshed, indent=2, ensure_ascii=False), encoding="utf-8")
    return RedirectResponse(url=return_to or f"/jobs/{job_id}", status_code=303)


@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request) -> HTMLResponse:
    ensure_private_profile()
    profile = load_profile_for_setup()
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "request": request,
            "env": dotenv_values(ROOT / ".env"),
            "files": setup_files(),
            "profile": profile,
            "model_options": CLAUDE_MODELS,
            "template_variables": TEMPLATE_VARIABLES,
            "cv_reference": get_cv_reference(),
            "sources": load_source_entries(),
        },
    )


@app.post("/setup/env")
def save_env(
    anthropic_api_key: str = Form(""),
    claude_model: str = Form("claude-sonnet-4-0"),
    claude_use_by_default: bool = Form(False),
) -> RedirectResponse:
    values = dict(dotenv_values(ROOT / ".env"))
    if anthropic_api_key:
        values["ANTHROPIC_API_KEY"] = anthropic_api_key
    values["CLAUDE_MODEL"] = claude_model
    values["CLAUDE_USE_BY_DEFAULT"] = "true" if claude_use_by_default else "false"
    write_env(values)
    return RedirectResponse(url="/setup", status_code=303)


@app.post("/setup/contact")
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
    path = ROOT / "profile" / "contact.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    contact = data.get("contact", {})
    contact.update(
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
            "first_name": name.split(" ")[0] if name else "",
            "last_name": name.split(" ")[-1] if name else "",
        }
    )
    data["contact"] = contact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return RedirectResponse(url="/setup#profile", status_code=303)


@app.post("/setup/preferences")
def save_preferences(
    available_from: str = Form(""),
    logistics: str = Form(""),
    current_base: str = Form(""),
    onsite_roles: str = Form(""),
    preferred_regions: str = Form(""),
    interests: str = Form(""),
    minimum_digest_score: int = Form(45),
) -> RedirectResponse:
    path = ROOT / "profile" / "preferences.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["availability"] = {"available_from": available_from, "logistics": logistics}
    data["location_policy"] = {
        "current_base": current_base,
        "onsite_roles": onsite_roles,
        "preferred_regions": lines_to_list(preferred_regions),
    }
    role_preferences = data.get("role_preferences", {})
    role_preferences["interests"] = lines_to_list(interests)
    data["role_preferences"] = role_preferences
    data["thresholds"] = {"minimum_digest_score": minimum_digest_score}
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return RedirectResponse(url="/setup#profile", status_code=303)


@app.post("/setup/cv-reference")
async def upload_cv_reference(cv_file: UploadFile = File(...), extract_to_canonical: bool = Form(False)) -> RedirectResponse:
    if not cv_file.filename:
        return RedirectResponse(url="/setup#cv-reference", status_code=303)
    suffix = Path(cv_file.filename).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise HTTPException(status_code=400, detail="Supported CV reference formats: PDF, DOCX, TXT, MD")
    target_dir = ROOT / "profile" / "files"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"reference-cv{suffix}"
    target.write_bytes(await cv_file.read())
    text = extract_cv_text(target)
    if text:
        (target_dir / "reference-cv-extracted.txt").write_text(text, encoding="utf-8")
    if extract_to_canonical and text:
        (ROOT / "profile" / "canonical-cv.md").write_text(text.strip() + "\n", encoding="utf-8")
    return RedirectResponse(url="/setup#cv-reference", status_code=303)


@app.get("/profile-files/{filename}")
def profile_file(filename: str) -> FileResponse:
    target_dir = (ROOT / "profile" / "files").resolve()
    path = (target_dir / filename).resolve()
    if not str(path).startswith(str(target_dir)) or not path.exists():
        raise HTTPException(status_code=404, detail="Profile file not found")
    return FileResponse(path, filename=path.name)


@app.post("/setup/source-toggle")
def toggle_source(index: int = Form(...), enabled: bool = Form(False)) -> RedirectResponse:
    path = ROOT / "sources" / "recruiting-sites.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"sources": []}
    sources = data.get("sources", [])
    if index < 0 or index >= len(sources):
        raise HTTPException(status_code=400, detail="Invalid source index")
    sources[index]["enabled"] = enabled
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return RedirectResponse(url="/setup#sources", status_code=303)


@app.post("/setup/source-add")
def add_source(
    name: str = Form(...),
    url: str = Form(""),
    source_type: str = Form("generic_html"),
    keywords: str = Form(""),
    enabled: bool = Form(True),
) -> RedirectResponse:
    path = ROOT / "sources" / "recruiting-sites.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"sources": []}
    entry = {
        "name": name,
        "type": source_type,
        "enabled": enabled,
    }
    if url:
        entry["url"] = url
    keyword_list = lines_to_list(keywords)
    if keyword_list:
        entry["keywords"] = keyword_list
    data.setdefault("sources", []).append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return RedirectResponse(url="/setup#sources", status_code=303)


@app.post("/setup/file")
def save_setup_file(file_key: str = Form(...), content: str = Form(...)) -> RedirectResponse:
    files = setup_files()
    if file_key not in files:
        raise HTTPException(status_code=400, detail="Unsupported setup file")
    path = ROOT / files[file_key]["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return RedirectResponse(url="/setup", status_code=303)


def list_packages(run_id: str = "") -> list[dict[str, Any]]:
    packages = []
    for path in (ROOT / "output").glob("*/*/index.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if run_id and item.get("run_id") != run_id:
            continue
        status = ApplicationStatusStore(ROOT).get(item.get("stable_id", ""))
        if status:
            item["application_status"] = status.status
        item["_index_path"] = str(path)
        packages.append(item)
    packages.sort(key=lambda item: (item.get("match_score", 0), item.get("title", "")), reverse=True)
    return packages


def list_unique_jobs() -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for package in list_packages():
        stable_id = package.get("stable_id") or package.get("package_id")
        existing = by_id.get(stable_id)
        if not existing or package.get("run_id", "") > existing.get("run_id", ""):
            by_id[stable_id] = package
    return sorted(by_id.values(), key=lambda item: (item.get("match_score", 0), item.get("title", "")), reverse=True)


def build_dashboard_stats(runs) -> dict[str, Any]:
    today = datetime.now().date().isoformat()
    latest_run = runs[0] if runs else None
    latest_is_today = bool(latest_run and latest_run.started_at.startswith(today))
    active_run = next((run for run in runs if run.status in {"pending", "running"}), None)
    today_runs = [run for run in runs if run.started_at.startswith(today)]
    statuses = ApplicationStatusStore(ROOT).list_all()
    seven_days_ago = datetime.now(timezone.utc).timestamp() - 7 * 24 * 60 * 60
    applied_last_7 = 0
    for record in statuses:
        if record.status != "applied" or not record.applied_at:
            continue
        try:
            applied_at = datetime.fromisoformat(record.applied_at).timestamp()
        except ValueError:
            continue
        if applied_at >= seven_days_ago:
            applied_last_7 += 1
    unique_jobs = list_unique_jobs()
    return {
        "latest_is_today": latest_is_today,
        "active_run": active_run,
        "jobs_found_today": sum(run.total_loaded for run in today_runs),
        "new_roles_today": sum(run.new_roles for run in today_runs),
        "unseen_jobs": sum(1 for job in unique_jobs if job.get("state") in {"new", "changed"} and job.get("application_status") == "unreviewed"),
        "applied_last_7": applied_last_7,
    }


def find_package(job_id: str, run_id: str = "") -> dict[str, Any] | None:
    for package in list_packages(run_id):
        if package.get("stable_id") == job_id or package.get("package_id") == job_id:
            return package
    return None


def read_package_files(package: dict[str, Any]) -> dict[str, str]:
    result = {}
    for key, path_text in package.get("paths", {}).items():
        path = Path(path_text)
        if path.exists():
            result[key] = path.read_text(encoding="utf-8")
    return result


def build_review_bundle(package: dict[str, Any], files: dict[str, str], status) -> str:
    parts = [
        "# External Agent Review Bundle",
        "",
        "Please review and suggest improvements to the application materials below. Keep claims accurate and do not invent experience.",
        "",
        "## Role",
        f"Title: {package.get('title', '')}",
        f"Company/recruiter: {package.get('company', '')} / {package.get('recruiter', '')}",
        f"Location: {package.get('location', '')}",
        f"Remote/onsite: {package.get('remote', '')}",
        f"Rate: {package.get('rate', '')}",
        f"Source URL: {package.get('source_url', '')}",
        f"Application URL: {package.get('application_url', '')}",
        "",
        "## Match",
        f"Score/category: {package.get('match_score', '')}% / {package.get('match_category', '')}",
        f"Recommended angle: {package.get('recommended_angle', '')}",
        "Concerns: " + "; ".join(package.get("concerns", [])),
        f"Application status: {getattr(status, 'status', 'unreviewed') if status else 'unreviewed'}",
        "",
        "## Job JSON",
        files.get("job", ""),
        "",
        "## Match Analysis",
        files.get("match_analysis", "[Not generated yet]"),
        "",
        "## At-a-glance CV",
        files.get("cv", "[Not generated yet]"),
        "",
        "## Application Text",
        files.get("application", "[Not generated yet]"),
        "",
        "## Form Answers",
        files.get("form_answers", "[Not generated yet]"),
    ]
    return "\n".join(parts)


def mark_package_materials_generated(package: dict[str, Any], generated: bool) -> None:
    index_path = package.get("_index_path")
    if not index_path:
        return
    package["materials_generated"] = generated
    Path(index_path).write_text(json.dumps({key: value for key, value in package.items() if key != "_index_path"}, indent=2, ensure_ascii=False), encoding="utf-8")


def infer_package_date(package: dict[str, Any]):
    from datetime import date

    index_path = Path(package.get("_index_path", ""))
    for parent in index_path.parents:
        try:
            return date.fromisoformat(parent.name)
        except ValueError:
            continue
    return date.today()


def refresh_package_status(job_id: str, status: str) -> None:
    for path in (ROOT / "output").glob("*/*/index.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if item.get("stable_id") == job_id:
            item["application_status"] = status
            path.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")


def markdown_to_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = escaped.replace("\n### ", "\n<h3>").replace("\n## ", "\n<h2>").replace("\n# ", "\n<h1>")
    lines = escaped.splitlines()
    html_lines = []
    for line in lines:
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("- "):
            html_lines.append(f"<p class=\"bullet\">{line}</p>")
        elif line.strip():
            html_lines.append(f"<p>{line}</p>")
    return "\n".join(html_lines)


def ensure_private_profile() -> None:
    profile = ROOT / "profile"
    if not profile.exists() and (ROOT / "profile.example").exists():
        shutil.copytree(ROOT / "profile.example", profile)


def setup_files() -> dict[str, dict[str, str]]:
    return {
        "skills": {"label": "Skills and caveats", "field_id": "profile.skills", "path": "profile/skills.yaml", "content": read_text("profile/skills.yaml"), "help": "Skills are used by scoring and generation. Keep caveats honest; they are explicitly referenced in application text."},
        "experience": {"label": "Experience", "field_id": "profile.experience", "path": "profile/experience.yaml", "content": read_text("profile/experience.yaml"), "help": "Experience entries are scored by keywords. The two most relevant entries are selected for the at-a-glance CV."},
        "canonical_cv": {"label": "Canonical CV text", "field_id": "profile.canonical_cv", "path": "profile/canonical-cv.md", "content": read_text("profile/canonical-cv.md"), "help": "This is the main source-of-truth text given to Claude for writing."},
        "writing_style": {"label": "Writing style", "field_id": "profile.writing_style", "path": "profile/writing-style.md", "content": read_text("profile/writing-style.md"), "help": "Used in Claude prompts and as guidance for deterministic writing."},
        "sources": {"label": "Sources", "field_id": "sources", "path": "sources/recruiting-sites.yaml", "content": read_text("sources/recruiting-sites.yaml"), "help": "Enabled sources are read by the run service. local_yaml is safest; generic_html is best-effort."},
        "cv_template": {"label": "At-a-glance CV template", "field_id": "template.cv", "path": "templates/at-a-glance-cv.md.j2", "content": read_text("templates/at-a-glance-cv.md.j2"), "help": "Jinja template. Use {{ contact.name }}, {{ top_skills }}, {{ selected_experience }}, etc."},
        "application_template": {"label": "Application template", "field_id": "template.application", "path": "templates/application-letter.md.j2", "content": read_text("templates/application-letter.md.j2"), "help": "Deterministic fallback template used when Claude is disabled or fails."},
        "form_template": {"label": "Form answers template", "field_id": "template.form", "path": "templates/form-answers.md.j2", "content": read_text("templates/form-answers.md.j2"), "help": "Standard form answer package. Do not imply actual form inspection here."},
        "application_prompt": {"label": "Claude application prompt", "field_id": "prompt.application", "path": "prompts/generate_application.md", "content": read_text("prompts/generate_application.md"), "help": "Prompt template for Claude application generation. Variables use Python .format style: {canonical_cv}, {title}, {description}."},
    }


def load_profile_for_setup() -> dict[str, Any]:
    ensure_private_profile()
    return {
        "contact": load_yaml_file("profile/contact.yaml").get("contact", {}),
        "preferences": load_yaml_file("profile/preferences.yaml"),
    }


def load_yaml_file(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def lines_to_list(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def get_cv_reference() -> dict[str, str]:
    target_dir = ROOT / "profile" / "files"
    for path in sorted(target_dir.glob("reference-cv.*")) if target_dir.exists() else []:
        if path.suffix.lower() in {".pdf", ".docx", ".txt", ".md"}:
            extracted = target_dir / "reference-cv-extracted.txt"
            return {
                "filename": path.name,
                "path": str(path),
                "url": f"/profile-files/{path.name}",
                "extracted_path": str(extracted) if extracted.exists() else "",
                "extracted_text": extracted.read_text(encoding="utf-8") if extracted.exists() else "",
            }
    return {}


def load_source_entries() -> list[dict[str, Any]]:
    data = load_yaml_file("sources/recruiting-sites.yaml")
    entries = []
    for index, source in enumerate(data.get("sources", [])):
        item = dict(source)
        item["_index"] = index
        entries.append(item)
    return entries


def extract_cv_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md"}:
            return path.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        if suffix == ".docx":
            from docx import Document

            document = Document(str(path))
            return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()).strip()
    except Exception as exc:
        return f"[Text extraction failed for {path.name}: {exc}]"
    return ""


def read_text(relative_path: str) -> str:
    path = ROOT / relative_path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_env(values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items() if value is not None]
    (ROOT / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def provider_context_for(field_id: str) -> str:
    from job_agent.prompt_context import FIELD_CONTEXTS

    return FIELD_CONTEXTS.get(field_id, "We are editing a text field in Job Agent.")


if __name__ == "__main__":
    uvicorn.run("job_agent.web.app:app", host="127.0.0.1", port=int(os.getenv("JOB_AGENT_PORT", "8765")), reload=False)
