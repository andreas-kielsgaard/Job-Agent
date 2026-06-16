from __future__ import annotations

import json
import queue
import threading
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from job_agent.llm import ExternalAgentService, LlmRequest
from job_agent.paths import resolve_project_path
from job_agent.services.recipe_artifact_service import RecipeArtifactService
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_suggestion_service import (
    build_batch_recipe_suggestion_prompt,
    build_recipe_suggestion_prompt,
    load_recipe_suggestion_evidence,
    parse_batch_recipe_suggestion_response,
    parse_recipe_suggestion_response,
)
from job_agent.services.source_registry_service import SOURCE_KIND_DEFINITIONS, SOURCE_STATUS_DEFINITIONS
from job_agent.web.dependencies import templates, workflow_handler
from job_agent.web.form_options import recipe_options
from job_agent.web.runtime import runtime
from job_agent.web.source_workflow import SourceWorkflowHandler

router = APIRouter()


@router.get("/sources", response_class=HTMLResponse)
def source_overview(
    request: Request,
    message: str = "",
    warning: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "sources.html",
        {"request": request, **workflow_handler().source.overview_context(message=message, warning=warning)},
    )


@router.get("/api/sources/overview")
def source_overview_payload(request: Request) -> JSONResponse:
    context = {"request": request, **workflow_handler().source.overview_context()}
    response = JSONResponse(
        {
            "overview_html": _render_template_fragment("source_overview_dynamic.html", context),
            "prepare_all_html": _render_template_fragment("source_overview_prepare_all_action.html", context),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/sources/new", response_class=HTMLResponse)
def new_source_form(
    request: Request,
    name: str = "",
    url: str = "",
    recipe_path: str = "",
    notes: str = "",
    warning: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "source_new.html",
        {
            "request": request,
            "title": "Add Source",
            "name": name,
            "url": url,
            "recipe_path": recipe_path,
            "notes": notes,
            "warning": warning,
            "recipe_options": recipe_options(workflow_handler().source.root),
            "source_auto_setup_configured": workflow_handler().auto_setup.is_configured(),
        },
    )


@router.get("/sources/suggest", response_class=HTMLResponse)
def suggest_sources_form(
    request: Request,
    focus: str = "",
    interaction_id: str = "",
    message: str = "",
    warning: str = "",
) -> HTMLResponse:
    context = workflow_handler().source.suggestion_context(focus=focus, message=message, warning=warning)
    if interaction_id:
        try:
            result = workflow_handler().source.load_external_source_suggestion_result(interaction_id)
            context = workflow_handler().source.suggestion_context(
                focus=result.focus,
                raw_response=result.raw_response,
                suggestions=result.suggestions,
                message=f"Parsed {len(result.suggestions)} source suggestions from the external agent.",
                model=result.model,
            )
        except (KeyError, ValueError) as exc:
            context = workflow_handler().source.suggestion_context(
                focus=focus,
                warning=f"Could not load external-agent suggestions: {exc}",
            )
    return templates.TemplateResponse(
        request,
        "source_suggestions.html",
        {"request": request, **context},
    )


@router.post("/sources/suggest/generate", response_class=HTMLResponse)
def generate_source_suggestions(
    request: Request,
    focus: str = Form(""),
    llm_model: str = Form(""),
) -> HTMLResponse:
    try:
        result = workflow_handler().source.suggest_sources_with_llm(focus=focus, llm_model=llm_model)
        context = workflow_handler().source.suggestion_context(
            focus=focus,
            raw_response=result.raw_response,
            suggestions=result.suggestions,
            message=f"Generated {len(result.suggestions)} source suggestions.",
            model=result.model,
        )
    except (RuntimeError, ValueError) as exc:
        raw_response = str(getattr(exc, "raw_response", "") or "")
        context = workflow_handler().source.suggestion_context(
            focus=focus,
            raw_response=raw_response,
            warning=f"Could not generate with the connected LLM: {exc}",
        )
    return templates.TemplateResponse(
        request,
        "source_suggestions.html",
        {"request": request, **context},
    )


@router.post("/sources/suggest/parse", response_class=HTMLResponse)
def parse_source_suggestions(
    request: Request,
    focus: str = Form(""),
    llm_response: str = Form(""),
) -> HTMLResponse:
    try:
        suggestions = workflow_handler().source.parse_source_suggestions(llm_response)
        context = workflow_handler().source.suggestion_context(
            focus=focus,
            raw_response=llm_response,
            suggestions=suggestions,
            message=f"Parsed {len(suggestions)} source suggestions.",
        )
    except ValueError as exc:
        context = workflow_handler().source.suggestion_context(
            focus=focus,
            raw_response=llm_response,
            warning=str(exc),
        )
    return templates.TemplateResponse(
        request,
        "source_suggestions.html",
        {"request": request, **context},
    )


@router.post("/sources/suggest/external-agent/prepare")
def prepare_external_source_suggestions(focus: str = Form("")) -> JSONResponse:
    try:
        interaction = workflow_handler().source.prepare_external_source_suggestions(focus=focus)
    except (RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, **interaction.to_payload()})


@router.post("/sources/suggest/external-agent/apply")
def apply_external_source_suggestions(
    interaction_id: str = Form(...),
    response_text: str = Form(...),
) -> JSONResponse:
    try:
        result = workflow_handler().source.apply_external_source_suggestions(interaction_id, response_text)
    except (KeyError, RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "redirect_url": f"/sources/suggest?{urlencode({'interaction_id': interaction_id})}",
            "suggestion_count": len(result.suggestions),
        }
    )


@router.post("/sources/suggest/save")
def save_suggested_source(
    name: str = Form(""),
    url: str = Form(""),
    notes: str = Form(""),
) -> JSONResponse:
    handler = workflow_handler().source
    disqualification = handler.source_disqualification(url)
    if disqualification:
        return JSONResponse(
            {
                "ok": False,
                "error": f"Domain is disqualified from source suggestions: {disqualification.reason}",
            },
            status_code=400,
        )
    existing = handler.existing_source_by_url(url)
    if not existing:
        existing = handler.existing_source_by_domain(url)
    if existing:
        return JSONResponse(
            {
                "ok": True,
                "status": "already_added",
                "source_id": existing.id,
                "source_name": existing.name,
                "source_url": f"/sources/{existing.id}",
                "message": f"Already added to pending setup as {existing.name}.",
            }
        )
    try:
        created = handler.add_source(name=name, url=url, recipe_path="", notes=notes)
    except ValueError as exc:
        existing = handler.existing_source_by_url(url)
        if existing:
            return JSONResponse(
                {
                    "ok": True,
                    "status": "already_added",
                    "source_id": existing.id,
                    "source_name": existing.name,
                    "source_url": f"/sources/{existing.id}",
                    "message": f"Already added to pending setup as {existing.name}.",
                }
            )
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "status": "added",
            "source_id": created.id,
            "source_name": created.name,
            "source_url": f"/sources/{created.id}",
            "message": "Added to pending setup. It is not included in daily runs yet.",
        }
    )


@router.post("/sources/suggest/disqualify")
def disqualify_suggested_domain(
    domain: str = Form(""),
    reason: str = Form(""),
) -> JSONResponse:
    try:
        record = workflow_handler().source.disqualify_domain(domain, reason=reason)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "domain": record.domain,
            "reason": record.reason,
            "message": f"Disqualified {record.domain} from future source suggestions.",
        }
    )


@router.post("/sources/disqualify-domain")
def disqualify_domain(
    domain: str = Form(""),
    reason: str = Form(""),
) -> RedirectResponse:
    try:
        record = workflow_handler().source.disqualify_domain(domain, reason=reason)
    except ValueError as exc:
        return _redirect_to_sources(warning=f"Could not disqualify domain: {exc}")
    return _redirect_to_sources(message=f"Disqualified {record.domain} from future source suggestions.")


@router.post("/sources/new", response_class=HTMLResponse)
def create_source(
    request: Request,
    name: str = Form(""),
    url: str = Form(""),
    recipe_path: str = Form(""),
    notes: str = Form(""),
    auto_setup: str = Form(""),
):
    try:
        created = workflow_handler().source.add_source(
            name=name,
            url=url,
            recipe_path=recipe_path,
            notes=notes,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "source_new.html",
            {
                "request": request,
                "title": "Add Source",
                "name": name,
                "url": url,
                "recipe_path": recipe_path,
                "notes": notes,
                "warning": str(exc),
                "recipe_options": recipe_options(workflow_handler().source.root),
                "source_auto_setup_configured": workflow_handler().auto_setup.is_configured(),
            },
            status_code=400,
        )
    if auto_setup:
        try:
            task = runtime.launch_source_auto_setup(created.id)
        except (RuntimeError, ValueError) as exc:
            return _redirect_to_source(created.id, warning=f"Source saved, but automatic setup could not start: {exc}")
        return _redirect_to_source(
            created.id,
            message=f"Source added and automatic setup started for {task.source_name}.",
            fragment="auto-setup",
        )
    message = "Source added. It is saved only for setup and is not included in the daily run yet."
    if created.recipe_path:
        message += " Review the reading plan next."
    else:
        message += " Teach the app how to read it next."
    return _redirect_to_source(created.id, message=message)


@router.get("/sources/auto-setup", response_class=HTMLResponse)
def source_auto_setup_monitor(
    request: Request,
    source_id: str = "",
    queue: str = "",
    llm_model: str = "",
    message: str = "",
    warning: str = "",
) -> HTMLResponse:
    context = workflow_handler().auto_setup.monitor_context(
        source_id=source_id,
        message=message,
        warning=warning,
    )
    response = templates.TemplateResponse(
        request,
        "source_auto_setup_runs.html",
        {"request": request, "auto_setup_queue": queue, "auto_setup_llm_model": llm_model, **context},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/sources/auto-setup/status")
def source_auto_setup_status(source_id: str = "") -> JSONResponse:
    return JSONResponse(workflow_handler().auto_setup.monitor_payload(source_id=source_id))


@router.post("/api/sources/auto-setup/start-all")
def api_start_all_source_auto_setups(llm_model: str = Form("")) -> JSONResponse:
    try:
        tasks = runtime.launch_all_source_auto_setups(llm_model=llm_model)
    except (RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "warning": f"Automatic setup could not start: {exc}"}, status_code=400)
    if not tasks:
        return JSONResponse(
            {"ok": True, "queued_count": 0, "warning": "No setup-ready source URLs need automatic setup."}
        )
    return JSONResponse(
        {
            "ok": True,
            "queued_count": len(tasks),
            "message": f"Automatic setup queued for {len(tasks)} source(s).",
        }
    )


@router.get("/sources/{source_id}", response_class=HTMLResponse)
def source_detail(request: Request, source_id: str, message: str = "", warning: str = "") -> HTMLResponse:
    handler = workflow_handler()
    workflow = handler.source.build(source_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Source not found.")
    response = templates.TemplateResponse(
        request,
        "source_detail.html",
        {
            "request": request,
            "execution_message": message,
            "execution_warning": warning,
            "recipe_options": recipe_options(handler.source.root),
            "kind_options": SOURCE_KIND_DEFINITIONS,
            "status_options": SOURCE_STATUS_DEFINITIONS,
            "source_auto_setup": handler.auto_setup.context_for_state(workflow),
            **workflow.template_context(),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/sources/{source_id}/auto-setup/start")
def start_source_auto_setup(source_id: str, llm_model: str = Form("")) -> RedirectResponse:
    try:
        task = runtime.launch_source_auto_setup(source_id, llm_model=llm_model)
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Automatic setup could not start: {exc}", fragment="auto-setup")
    return RedirectResponse(
        f"/sources/auto-setup?{urlencode({'source_id': source_id, 'message': f'Automatic setup started for {task.source_name}.'})}",
        status_code=303,
    )


@router.post("/sources/auto-setup/start-all")
def start_all_source_auto_setups(llm_model: str = Form("")) -> RedirectResponse:
    return RedirectResponse(
        f"/sources/auto-setup?{urlencode({'queue': 'all', 'llm_model': llm_model, 'message': 'Automatic setup overview opened. Queuing source setup...'})}",
        status_code=303,
    )


@router.post("/sources/{source_id}/auto-setup/continue")
def continue_source_auto_setup(
    source_id: str,
    run_id: str = Form(""),
    llm_model: str = Form(""),
) -> RedirectResponse:
    try:
        task = runtime.launch_source_auto_setup(source_id, run_id=run_id, llm_model=llm_model)
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(
            source_id, warning=f"Automatic setup could not continue: {exc}", fragment="auto-setup"
        )
    return RedirectResponse(
        f"/sources/auto-setup?{urlencode({'source_id': source_id, 'message': f'Automatic setup continuing for {task.source_name}.'})}",
        status_code=303,
    )


@router.post("/sources/{source_id}/registry/update")
def update_registry_source(
    source_id: str,
    name: str = Form(...),
    kind: str = Form(...),
    url: str = Form(""),
    status: str = Form(...),
    recipe_path: str = Form(""),
    notes: str = Form(""),
) -> RedirectResponse:
    try:
        workflow_handler().source.update_source(
            source_id,
            name=name,
            kind=kind,
            url=url,
            status=status,
            recipe_path=recipe_path,
            notes=notes,
        )
    except (KeyError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Source update failed: {exc}")
    return _redirect_to_source(source_id, message="Source registry updated.")


@router.post("/sources/{source_id}/archive")
def archive_registry_source(source_id: str) -> RedirectResponse:
    try:
        _source, disabled = workflow_handler().source.archive_source(source_id)
    except KeyError as exc:
        return _redirect_to_sources(warning=f"Source archive failed: {exc}")
    suffix = " Existing daily-run projection was disabled." if disabled else ""
    return _redirect_to_sources(
        message=f"Source archived. It is hidden from normal source lists and blocked from daily-run enablement.{suffix}",
    )


@router.post("/sources/{source_id}/restore")
def restore_registry_source(source_id: str) -> RedirectResponse:
    try:
        workflow_handler().source.restore_source(source_id)
    except KeyError as exc:
        return _redirect_to_sources(warning=f"Source restore failed: {exc}")
    return _redirect_to_source(source_id, message="Source restored to Needs review.")


@router.post("/sources/{source_id}/recipe-calibration/capture")
def capture_recipe_calibration_artifact(
    source_id: str,
    rendered: str = Form(""),
    capture_detail: str = Form(""),
    max_candidates: int = Form(30),
) -> RedirectResponse:
    try:
        result = workflow_handler().recipe.capture_calibration(
            source_id,
            rendered=True if rendered else None,
            capture_detail=bool(capture_detail),
            max_candidates=max_candidates,
        )
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Calibration capture failed: {exc}")
    artifact_label = _display_path(result.artifact_dir, workflow_handler().recipe.root)
    message = (
        f"Calibration artifact captured: {artifact_label}. "
        f"{result.candidate_count} candidate regions; recipe extracted {result.recipe_extracted_count} jobs."
    )
    if result.detail_sample_url:
        message += " One detail page sample was captured."
    return _redirect_to_source(source_id, message=message)


@router.post("/sources/{source_id}/reading-plan/learn")
def learn_source_reading_plan(
    source_id: str,
    rendered: str = Form("auto"),
    capture_detail: str = Form("1"),
    max_candidates: int = Form(30),
    refine: str = Form("1"),
    max_attempts: int = Form(3),
    llm_model: str = Form(""),
) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_not_archived(source)
    if not source.url:
        return _redirect_to_source(source_id, warning="Save a source URL before teaching the app how to read it.")
    bounded_candidates = max(5, min(max_candidates, 50))
    try:
        run = workflow_handler().recipe.start_from_source_capture(
            source_id,
            rendered=_rendered_form_value(rendered),
            capture_detail=bool(capture_detail),
            max_candidates=bounded_candidates,
            refine=bool(refine),
            max_attempts=max_attempts,
            llm_model=llm_model,
        )
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Could not learn this source yet: {exc}")
    return RedirectResponse(f"/sources/{source_id}/recipe-generation/{run['run_id']}", status_code=303)


@router.post("/sources/{source_id}/reading-plan/rebuild-from-test")
def rebuild_source_reading_plan_from_test(source_id: str, llm_model: str = Form("")) -> RedirectResponse:
    workflow = _source_workflow(source_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Source not found.")
    source = workflow.source
    _require_not_archived(source)
    if not source.url:
        return _redirect_to_source(source_id, warning="Save a source URL before rebuilding the reading plan.")
    if workflow.readiness.checks.get("recipe_changed_after_source_test"):
        return RedirectResponse(
            f"/sources/{source_id}/test-run?{urlencode({'start': '1'})}",
            status_code=303,
        )
    insight = workflow.source_test_insight
    try:
        run = workflow_handler().recipe.start_from_source_capture(
            source_id,
            rendered=None,
            capture_detail=True,
            max_candidates=50,
            refine=True,
            max_attempts=4,
            source_test_insight=insight.get("generation_clues", insight),
            llm_model=llm_model,
        )
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Could not rebuild this reading plan yet: {exc}")
    return RedirectResponse(f"/sources/{source_id}/recipe-generation/{run['run_id']}", status_code=303)


@router.post("/sources/{source_id}/recipe/update")
def update_source_recipe(
    source_id: str,
    recipe_path: str = Form(""),
) -> RedirectResponse:
    try:
        workflow_handler().source.update_source_recipe(source_id, recipe_path)
    except (KeyError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Recipe update failed: {exc}")
    return _redirect_to_source(source_id, message="Source recipe updated.")


@router.post("/sources/{source_id}/execution/create")
def create_execution_source(source_id: str) -> RedirectResponse:
    try:
        workflow_handler().source.create_or_update_execution_source(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _redirect_to_source(
        source_id,
        message=(
            "Disabled daily-run projection prepared in sources/recruiting-sites.yaml. "
            "It will not run until you explicitly enable it after readiness checks."
        ),
    )


@router.post("/sources/{source_id}/execution/update")
def update_execution_source(source_id: str) -> RedirectResponse:
    try:
        workflow_handler().source.create_or_update_execution_source(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _redirect_to_source(source_id, message="Daily-run projection updated and kept disabled.")


@router.get("/sources/{source_id}/session", response_class=HTMLResponse)
def source_session_form(request: Request, source_id: str, message: str = "", warning: str = "") -> HTMLResponse:
    try:
        context = workflow_handler().source.source_session_context(source_id, message=message, warning=warning)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "source_session.html",
        {"request": request, **context},
    )


@router.post("/sources/{source_id}/session/connect")
def connect_source_session(
    source_id: str,
    storage_state_path: str = Form(""),
    expires_at: str = Form(""),
) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    status = workflow_handler().source.record_source_session(
        source.id,
        storage_state_path=storage_state_path,
        expires_at=expires_at,
    )
    if not status.usable:
        return RedirectResponse(
            f"/sources/{source.id}/session?{urlencode({'warning': status.summary})}",
            status_code=303,
        )
    return RedirectResponse(
        f"/sources/{source.id}/session?{urlencode({'message': 'Source session connected. Verify it before daily runs rely on it.'})}",
        status_code=303,
    )


@router.post("/sources/{source_id}/session/capture")
def capture_source_session(
    source_id: str,
    storage_state_path: str = Form(""),
    expires_at: str = Form(""),
) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    task = workflow_handler().source.launch_source_session_capture(
        runtime,
        source.id,
        storage_state_path=storage_state_path,
        expires_at=expires_at,
    )
    return RedirectResponse(
        f"/sources/{source.id}/session?{urlencode({'message': f'Opening sign-in browser. Task {task.task_id} will save the session when the window is closed.'})}",
        status_code=303,
    )


@router.post("/sources/{source_id}/session/verify")
def verify_source_session(source_id: str) -> RedirectResponse:
    try:
        execution = workflow_handler().source.verify_source_session(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    source = execution.source
    if workflow_handler().source.source_test_verified_session(execution.result):
        message = "Session verified. The source test can use it successfully."
        if execution.readiness.readiness_status == "ready":
            message += " This source is ready for the next setup step."
        return RedirectResponse(
            f"/sources/{source.id}/session?{urlencode({'message': message})}",
            status_code=303,
        )
    warning = (
        execution.session_status.last_error or "The session could not be verified yet. Check the source test result."
    )
    return RedirectResponse(
        f"/sources/{source.id}/session?{urlencode({'warning': warning})}",
        status_code=303,
    )


@router.post("/sources/{source_id}/session/clear")
def clear_source_session(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    workflow_handler().source.clear_source_session(source.id)
    return _redirect_to_source(source.id, message="Source session cleared.")


@router.post("/sources/{source_id}/execution/enable")
def enable_execution_source(source_id: str, return_to: str = Form("")) -> RedirectResponse:
    try:
        result = workflow_handler().source.enable_when_ready(source_id)
    except RuntimeError as exc:
        return _redirect_to_source_action(source_id, return_to=return_to, warning=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.enabled:
        warning = " ".join(result.check.blockers[:3]) or "Source is not ready for daily-run enablement."
        return _redirect_to_source_action(source_id, return_to=return_to, warning=warning)
    return _redirect_to_source_action(source_id, return_to=return_to, message="Source included in daily runs.")


@router.post("/sources/{source_id}/execution/disable")
def disable_execution_source(source_id: str) -> RedirectResponse:
    try:
        workflow_handler().source.disable_execution_source(source_id)
    except (KeyError, ValueError):
        return _redirect_to_source(source_id, warning="This source is not available for daily-run disablement.")
    return _redirect_to_source(source_id, message="Source removed from daily runs.")


@router.get("/sources/{source_id}/test-run", response_class=HTMLResponse)
def source_test_run(request: Request, source_id: str) -> HTMLResponse:
    try:
        context = workflow_handler().source.source_test_context(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = templates.TemplateResponse(
        request,
        "source_test_run.html",
        {
            "request": request,
            **context,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/sources/{source_id}/test-run")
def run_source_test(source_id: str) -> JSONResponse:
    try:
        execution = workflow_handler().source.run_source_test(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(execution.payload)


@router.post("/sources/{source_id}/test-run/stream")
def run_source_test_stream(source_id: str) -> StreamingResponse:
    try:
        workflow_handler().source.source_test_context(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def stream_events():
        events: queue.Queue[dict | None] = queue.Queue()

        def progress(event: dict) -> None:
            events.put({"type": "progress", "event": event})

        def worker() -> None:
            try:
                execution = workflow_handler().source.run_source_test(source_id, progress_callback=progress)
                events.put({"type": "complete", "data": execution.payload})
            except Exception as exc:
                events.put({"type": "error", "message": str(exc)})
            finally:
                events.put(None)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            event = events.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/sources/{source_id}/enable-when-ready")
def enable_source_when_ready(source_id: str, return_to: str = Form("")) -> RedirectResponse:
    try:
        result = workflow_handler().source.enable_when_ready(source_id)
    except RuntimeError as exc:
        return _redirect_to_source_action(source_id, return_to=return_to, warning=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.enabled:
        warning = " ".join(result.check.blockers[:3]) or "Source is not ready for daily-run enablement."
        return _redirect_to_source_action(source_id, return_to=return_to, warning=warning)
    return _redirect_to_source_action(
        source_id,
        return_to=return_to,
        message="Source included in daily runs after readiness checks passed.",
    )


@router.post("/sources/{source_id}/run-now")
def run_source_now(source_id: str) -> RedirectResponse:
    try:
        result = workflow_handler().source.run_source_now(source_id)
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=str(exc))
    if result.run_detail_url:
        return RedirectResponse(result.run_detail_url, status_code=303)
    return _redirect_to_source(
        source_id,
        warning=f"Single-source run did not start: {result.status}.",
    )


@router.post("/sources/{source_id}/index-listings")
def index_source_listings(source_id: str) -> RedirectResponse:
    try:
        task = workflow_handler().source.launch_listing_index(runtime, source_id)
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=str(exc))
    return _redirect_to_source(source_id, message=f"Listing index refresh started for {task.source_name}.")


@router.post("/sources/{source_id}/investigate-all")
def investigate_all_source_jobs(source_id: str) -> RedirectResponse:
    try:
        record = workflow_handler().source.launch_detail_review(runtime, source_id)
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=str(exc))
    return RedirectResponse(f"/runs/{record.run_id}", status_code=303)


@router.post("/sources/{source_id}/recipe-candidates/generate")
def generate_recipe_candidate(
    source_id: str,
    artifact_dir: str = Form(...),
    refine: str = Form(""),
    max_attempts: int = Form(3),
    llm_model: str = Form(""),
) -> RedirectResponse:
    try:
        run = workflow_handler().recipe.start_from_artifact(
            source_id,
            artifact_dir=artifact_dir,
            refine=bool(refine),
            max_attempts=max_attempts,
            llm_model=llm_model,
        )
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Recipe candidate generation failed: {exc}")
    return RedirectResponse(f"/sources/{source_id}/recipe-generation/{run['run_id']}", status_code=303)


@router.post("/sources/{source_id}/recipe-candidates/external-agent/prepare")
def prepare_external_recipe_candidate(source_id: str, artifact_dir: str = Form(...)) -> JSONResponse:
    root = _workflow_handler().root
    source = _registry_source_or_404(source_id)
    _require_not_archived(source)
    try:
        artifact_path = RecipeArtifactService(root).resolve_artifact_path(artifact_dir)
        evidence = load_recipe_suggestion_evidence(
            artifact_path,
            source_name=source.name,
            start_url=source.url,
            existing_recipe_path=resolve_project_path(root, source.recipe_path) if source.recipe_path else None,
        )
        prompt = build_recipe_suggestion_prompt(evidence)
        interaction = ExternalAgentService(root).prepare(
            LlmRequest(prompt=prompt, max_tokens=2200, purpose="recipe_suggestion", run_id="manual"),
            title=f"Generate reading plan for {source.name}",
            instructions=(
                "Paste this prompt into an external agent. Paste back the full JSON response; "
                "the app will validate the suggested YAML and save it as a pending plan."
            ),
            metadata={
                "source_id": source_id,
                "artifact_dir": artifact_dir,
                "source_name": source.name,
                "start_url": source.url,
                "existing_recipe_path": source.recipe_path,
            },
        )
    except (RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, **interaction.to_payload()})


@router.post("/sources/{source_id}/recipe-candidates/external-agent/apply")
def apply_external_recipe_candidate(
    source_id: str,
    interaction_id: str = Form(...),
    response_text: str = Form(...),
) -> JSONResponse:
    root = _workflow_handler().root
    source = _registry_source_or_404(source_id)
    _require_not_archived(source)
    try:
        interaction = ExternalAgentService(root).load(interaction_id)
        if interaction.metadata.get("source_id") != source_id:
            raise ValueError("External-agent response does not belong to this source.")
        completion = ExternalAgentService(root).complete(interaction_id, response_text)
        artifact_dir = str(interaction.metadata.get("artifact_dir") or "")
        artifact_path = RecipeArtifactService(root).resolve_artifact_path(artifact_dir)
        result = parse_recipe_suggestion_response(
            artifact_path,
            completion.text,
            source_name=source.name,
            start_url=source.url,
            existing_recipe_path=resolve_project_path(root, source.recipe_path) if source.recipe_path else None,
        )
        candidate = RecipeCandidateStore(root).save_candidate_from_suggestion(result)
    except (KeyError, RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "redirect_url": f"/recipe-candidates/{candidate.candidate_id}?{urlencode({'source_id': source_id})}",
        }
    )


@router.post("/sources/{source_id}/recipe-candidates/external-agent/prepare-batch")
def prepare_external_recipe_candidate_batch(
    source_id: str,
    artifact_dirs: list[str] = Form(default=[]),
) -> JSONResponse:
    root = _workflow_handler().root
    source = _registry_source_or_404(source_id)
    _require_not_archived(source)
    try:
        selected = [value for value in artifact_dirs if str(value).strip()][:5]
        if not selected:
            raise ValueError("Select at least one saved sample.")
        evidences = []
        resolved_artifacts = []
        for artifact_dir in selected:
            artifact_path = RecipeArtifactService(root).resolve_artifact_path(artifact_dir)
            resolved_artifacts.append(str(artifact_path))
            evidences.append(
                load_recipe_suggestion_evidence(
                    artifact_path,
                    source_name=source.name,
                    start_url=source.url,
                    existing_recipe_path=resolve_project_path(root, source.recipe_path) if source.recipe_path else None,
                )
            )
        prompt = build_batch_recipe_suggestion_prompt(evidences)
        interaction = ExternalAgentService(root).prepare(
            LlmRequest(prompt=prompt, max_tokens=5200, purpose="recipe_suggestion_batch", run_id="manual"),
            title=f"Generate reading plans for {source.name}",
            instructions=(
                "Paste this prompt into an external agent. Paste back the full JSON response; "
                "the app will validate each suggested YAML item independently."
            ),
            metadata={
                "source_id": source_id,
                "artifact_dirs": resolved_artifacts,
                "source_name": source.name,
                "start_url": source.url,
                "existing_recipe_path": source.recipe_path,
            },
        )
    except (RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, **interaction.to_payload()})


@router.post("/sources/{source_id}/recipe-candidates/external-agent/apply-batch")
def apply_external_recipe_candidate_batch(
    source_id: str,
    interaction_id: str = Form(...),
    response_text: str = Form(...),
) -> JSONResponse:
    root = _workflow_handler().root
    source = _registry_source_or_404(source_id)
    _require_not_archived(source)
    try:
        service = ExternalAgentService(root)
        interaction = service.load(interaction_id)
        if interaction.metadata.get("source_id") != source_id:
            raise ValueError("External-agent response does not belong to this source.")
        completion = service.complete(interaction_id, response_text)
        artifact_dirs = interaction.metadata.get("artifact_dirs")
        if not isinstance(artifact_dirs, list) or not artifact_dirs:
            raise ValueError("External-agent batch interaction did not list calibration artifacts.")
        evidences = [
            load_recipe_suggestion_evidence(
                RecipeArtifactService(root).resolve_artifact_path(str(artifact_dir)),
                source_name=source.name,
                start_url=source.url,
                existing_recipe_path=resolve_project_path(root, source.recipe_path) if source.recipe_path else None,
            )
            for artifact_dir in artifact_dirs[:5]
        ]
        results = parse_batch_recipe_suggestion_response(evidences, completion.text)
        if not results:
            raise ValueError("External-agent response did not include any matching artifact items.")
        store = RecipeCandidateStore(root)
        candidates = [store.save_candidate_from_suggestion(result) for result in results]
    except (KeyError, RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "redirect_url": f"/sources/{source_id}?{urlencode({'message': f'Saved {len(candidates)} generated plan(s).'})}",
        }
    )


@router.post("/sources/{source_id}/recipe-generation/{run_id}/retry")
def retry_recipe_generation_run(
    source_id: str,
    run_id: str,
    llm_model: str = Form(""),
) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_not_archived(source)
    try:
        run = workflow_handler().recipe.retry_run(source_id, run_id, llm_model=llm_model)
    except (RuntimeError, ValueError) as exc:
        return RedirectResponse(
            f"/sources/{source_id}/recipe-generation/{run_id}?{urlencode({'warning': str(exc)})}",
            status_code=303,
        )
    return RedirectResponse(f"/sources/{source_id}/recipe-generation/{run['run_id']}", status_code=303)


@router.get("/sources/{source_id}/recipe-generation/{run_id}", response_class=HTMLResponse)
def recipe_generation_run_detail(request: Request, source_id: str, run_id: str, warning: str = "") -> HTMLResponse:
    source = _registry_source_or_404(source_id)
    try:
        run = workflow_handler().recipe.load_source_run(source_id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response = templates.TemplateResponse(
        request,
        "recipe_generation_run.html",
        {
            "request": request,
            "title": f"Reading Plan - {source.name}",
            "source": source,
            "run": run,
            "warning": warning,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/sources/{source_id}/recipe-generation/{run_id}/status")
def recipe_generation_run_status(source_id: str, run_id: str) -> JSONResponse:
    try:
        run = workflow_handler().recipe.load_source_run(source_id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(run)


@router.get("/recipe-candidates/{candidate_id}", response_class=HTMLResponse)
def recipe_candidate_detail(request: Request, candidate_id: str, source_id: str = "") -> HTMLResponse:
    try:
        context = workflow_handler().recipe.candidate_detail_context(candidate_id, source_id=source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "recipe_candidate_detail.html",
        {"request": request, **context},
    )


@router.post("/recipe-candidates/{candidate_id}/reject")
def reject_recipe_candidate(candidate_id: str, source_id: str = Form(""), reason: str = Form("")) -> RedirectResponse:
    try:
        workflow_handler().recipe.reject_candidate(candidate_id, reason=reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if source_id:
        return _redirect_to_source(source_id, message=f"Recipe candidate rejected: {candidate_id}")
    return RedirectResponse(f"/recipe-candidates/{candidate_id}", status_code=303)


@router.post("/recipe-candidates/{candidate_id}/adopt")
def adopt_recipe_candidate(
    candidate_id: str,
    source_id: str = Form(...),
    prepare_disabled_execution_entry: str = Form(""),
    next_action: str = Form(""),
) -> RedirectResponse:
    try:
        result = workflow_handler().recipe.adopt_candidate(
            candidate_id,
            source_id,
            prepare_disabled_execution_entry=bool(prepare_disabled_execution_entry),
        )
    except ValueError as exc:
        return _redirect_to_source(source_id, warning=f"Approved recipe adoption failed: {exc}")
    parts = [f"Approved recipe adopted for {result.source_name}."]
    if result.execution_entry_created:
        parts.append("Disabled daily-run projection created.")
    if result.execution_entry_updated:
        parts.append("Disabled daily-run projection updated.")
    parts.append("Next: run the safe source test for the selected plan.")
    if next_action == "test":
        return RedirectResponse(f"/sources/{source_id}/test-run?start=1", status_code=303)
    return _redirect_to_source(source_id, message=" ".join(parts), fragment="safe-test")


@router.post("/recipe-candidates/{candidate_id}/approve")
def approve_recipe_candidate(
    candidate_id: str,
    recipe_path: str = Form(...),
    source_id: str = Form(""),
    overwrite: str = Form(""),
    next_action: str = Form(""),
    allow_quality_warnings: str = Form(""),
) -> RedirectResponse:
    try:
        result = workflow_handler().recipe.approve_candidate(
            candidate_id,
            recipe_path,
            source_id=source_id,
            overwrite=bool(overwrite),
            allow_quality_warnings=bool(allow_quality_warnings),
        )
    except ValueError as exc:
        if source_id:
            return _redirect_to_source(source_id, warning=f"Recipe candidate approval failed: {exc}")
        return RedirectResponse(
            f"/recipe-candidates/{candidate_id}?{urlencode({'warning': str(exc)})}",
            status_code=303,
        )
    if source_id:
        try:
            adoption = workflow_handler().recipe.adopt_candidate(
                candidate_id,
                source_id,
                prepare_disabled_execution_entry=False,
            )
        except ValueError as exc:
            return _redirect_to_source(source_id, warning=f"Reading plan was saved, but could not be selected: {exc}")
        if next_action == "test":
            return RedirectResponse(f"/sources/{source_id}/test-run?start=1", status_code=303)
        return _redirect_to_source(
            source_id,
            message=(
                f"Reading plan saved and selected for {adoption.source_name}. "
                f"Review extracted {result.preview.extracted_job_count if result.preview else 0} jobs. "
                "Next: run the safe source test for the updated plan."
            ),
            fragment="safe-test",
        )
    return RedirectResponse(f"/recipe-candidates/{candidate_id}", status_code=303)


def _rendered_form_value(value: str) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"", "auto"}:
        return None
    return text in {"1", "true", "yes", "rendered"}


def _workflow_handler() -> SourceWorkflowHandler:
    return workflow_handler().source


def _source_workflow(source_id: str):
    return _workflow_handler().build(source_id)


def _registry_source_or_404(source_id: str):
    try:
        return _workflow_handler().require_source(source_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Source not found.") from None


def _require_not_archived(source) -> None:
    try:
        _workflow_handler().require_not_archived(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _redirect_to_source(
    source_id: str, *, message: str = "", warning: str = "", fragment: str = ""
) -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if warning:
        params["warning"] = warning
    suffix = f"?{urlencode(params)}" if params else ""
    anchor = f"#{fragment}" if fragment else ""
    return RedirectResponse(f"/sources/{source_id}{suffix}{anchor}", status_code=303)


def _redirect_to_source_action(
    source_id: str,
    *,
    return_to: str = "",
    message: str = "",
    warning: str = "",
    fragment: str = "",
) -> RedirectResponse:
    safe_return_to = _safe_sources_return_to(return_to)
    if not safe_return_to:
        return _redirect_to_source(source_id, message=message, warning=warning, fragment=fragment)
    params = {}
    if message:
        params["message"] = message
    if warning:
        params["warning"] = warning
    return RedirectResponse(_append_query_params(safe_return_to, params), status_code=303)


def _redirect_to_sources(
    *,
    message: str = "",
    warning: str = "",
) -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if warning:
        params["warning"] = warning
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/sources{suffix}", status_code=303)


def _safe_sources_return_to(value: str) -> str:
    text = str(value or "").strip()
    if not text or not text.startswith("/") or text.startswith("//") or "://" in text:
        return ""
    if text == "/sources" or text.startswith("/sources?") or text.startswith("/sources#"):
        return text
    return ""


def _append_query_params(return_to: str, params: dict[str, str]) -> str:
    if not params:
        return return_to
    base, separator, fragment = return_to.partition("#")
    query_separator = "&" if "?" in base else "?"
    suffix = f"{query_separator}{urlencode(params)}"
    return f"{base}{suffix}{separator}{fragment}" if separator else f"{base}{suffix}"


def _display_path(path, root) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _render_template_fragment(template_name: str, context: dict) -> str:
    return templates.env.get_template(template_name).render(**context)
