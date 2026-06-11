from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from job_agent.run_store import RunOptions
from job_agent.web.dependencies import templates, workflow_handler
from job_agent.web.runtime import runtime

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
    record = workflow_handler().executor.launch_daily_run(runtime, options)
    return RedirectResponse(url=f"/runs/{record.run_id}", status_code=303)


@router.get("/api/runs/{run_id}/status")
def run_status(run_id: str) -> JSONResponse:
    try:
        payload = workflow_handler().executor.run_status_payload(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None
    return JSONResponse(payload)


@router.get("/runs", response_class=HTMLResponse)
def run_list(request: Request, view: str = "active") -> HTMLResponse:
    return templates.TemplateResponse(
        request, "runs.html", {"request": request, **workflow_handler().executor.run_list_view(view)}
    )


@router.post("/api/runs/bulk")
def bulk_runs(
    run_ids: list[str] = Form(...), action: str = Form(...), return_to: str = Form("/runs")
) -> RedirectResponse:
    if action not in {"archive", "delete", "restore"}:
        raise HTTPException(status_code=400, detail="Unsupported bulk run action")
    workflow_handler().executor.apply_bulk_run_action(run_ids, action)
    return RedirectResponse(url=return_to, status_code=303)


@router.post("/api/runs/{run_id}/archive")
def archive_run(run_id: str, return_to: str = Form("/runs")) -> RedirectResponse:
    workflow_handler().executor.apply_run_action(run_id, "archive")
    return RedirectResponse(url=return_to, status_code=303)


@router.post("/api/runs/{run_id}/delete")
def delete_run(run_id: str, return_to: str = Form("/runs")) -> RedirectResponse:
    workflow_handler().executor.apply_run_action(run_id, "delete")
    return RedirectResponse(url=return_to, status_code=303)


@router.post("/api/runs/{run_id}/restore")
def restore_run(run_id: str, return_to: str = Form("/runs")) -> RedirectResponse:
    workflow_handler().executor.apply_run_action(run_id, "restore")
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
        view = workflow_handler().executor.run_detail_view(
            run_id,
            category,
            app_status,
            source,
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
    try:
        context = workflow_handler().executor.run_log_context(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None
    return templates.TemplateResponse(request, "log.html", {"request": request, **context})


@router.get("/api/runs/{run_id}/log")
def run_log_text(run_id: str) -> PlainTextResponse:
    try:
        log_text = workflow_handler().executor.run_log_text(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None
    return PlainTextResponse(log_text)
