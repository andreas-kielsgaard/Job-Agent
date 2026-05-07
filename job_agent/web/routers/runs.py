from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from job_agent.run_store import RunOptions
from job_agent.web.dependencies import current_root, run_store, templates
from job_agent.web.runtime import runtime
from job_agent.web.view_models.runs import build_run_detail_view, build_run_list_view

router = APIRouter()


@router.post("/api/run")
def launch_run(
    use_llm: bool = Form(False),
    ai_enhanced_search: bool = Form(False),
    include_seen: bool = Form(False),
    include_weak: bool = Form(False),
    mark_seen: bool = Form(False),
    generate_materials_option: bool = Form(False),
    is_test: bool = Form(False),
) -> RedirectResponse:
    options = RunOptions(
        use_llm=use_llm,
        ai_enhanced_search=ai_enhanced_search,
        include_seen=include_seen,
        include_weak=include_weak,
        mark_seen=mark_seen,
        generate_materials=generate_materials_option,
        is_test=is_test,
    )
    record = runtime.launch_daily_run(options)
    return RedirectResponse(url=f"/runs/{record.run_id}", status_code=303)


@router.get("/api/runs/{run_id}/status")
def run_status(run_id: str) -> JSONResponse:
    store = run_store()
    record = store.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    events = store.read_events(run_id, limit=20)
    latest = events[-1] if events else {}
    return JSONResponse({"run": record.__dict__, "latest_event": latest, "recent_events": events})


@router.get("/runs", response_class=HTMLResponse)
def run_list(request: Request, view: str = "active") -> HTMLResponse:
    return templates.TemplateResponse(
        request, "runs.html", {"request": request, **build_run_list_view(view, current_root())}
    )


@router.post("/api/runs/bulk")
def bulk_runs(
    run_ids: list[str] = Form(...), action: str = Form(...), return_to: str = Form("/runs")
) -> RedirectResponse:
    if action not in {"archive", "delete", "restore"}:
        raise HTTPException(status_code=400, detail="Unsupported bulk run action")
    store = run_store()
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


@router.post("/api/runs/{run_id}/archive")
def archive_run(run_id: str, return_to: str = Form("/runs")) -> RedirectResponse:
    run_store().archive(run_id)
    return RedirectResponse(url=return_to, status_code=303)


@router.post("/api/runs/{run_id}/delete")
def delete_run(run_id: str, return_to: str = Form("/runs")) -> RedirectResponse:
    run_store().soft_delete(run_id)
    return RedirectResponse(url=return_to, status_code=303)


@router.post("/api/runs/{run_id}/restore")
def restore_run(run_id: str, return_to: str = Form("/runs")) -> RedirectResponse:
    run_store().restore(run_id)
    return RedirectResponse(url=return_to, status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(
    request: Request,
    run_id: str,
    category: str = "",
    app_status: str = "",
    source: str = "",
    only_unreviewed: bool = False,
    ai_prioritized: bool = False,
    materials_missing: bool = False,
    match_group: str = "",
    generated: int = 0,
    failed: int = 0,
) -> HTMLResponse:
    try:
        view = build_run_detail_view(
            run_id,
            category,
            app_status,
            source,
            current_root(),
            only_unreviewed=only_unreviewed,
            ai_prioritized=ai_prioritized,
            materials_missing=materials_missing,
            match_group=match_group,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {"request": request, **view, "batch_result": {"generated": generated, "failed": failed}},
    )


@router.get("/runs/{run_id}/log", response_class=HTMLResponse)
def run_log(request: Request, run_id: str) -> HTMLResponse:
    record = run_store().get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    log_text = (
        Path(record.run_log_path).read_text(encoding="utf-8")
        if record.run_log_path and Path(record.run_log_path).exists()
        else ""
    )
    return templates.TemplateResponse(request, "log.html", {"request": request, "run": record, "log_text": log_text})


@router.get("/api/runs/{run_id}/log")
def run_log_text(run_id: str) -> PlainTextResponse:
    record = run_store().get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    path = Path(record.run_log_path)
    return PlainTextResponse(path.read_text(encoding="utf-8") if path.exists() else "")
