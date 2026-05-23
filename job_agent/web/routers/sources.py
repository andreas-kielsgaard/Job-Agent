from __future__ import annotations

import json
import queue
import threading
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from job_agent.services.recipe_calibration_service import capture_recipe_calibration
from job_agent.services.recipe_preview_service import explain_recipe
from job_agent.services.single_source_run_service import SingleSourceRunService
from job_agent.services.source_dry_run_service import SourceDryRunService
from job_agent.services.source_registry_service import SOURCE_KIND_DEFINITIONS, SOURCE_STATUS_DEFINITIONS
from job_agent.web.dependencies import (
    approved_recipe_adoption_service,
    execution_source_service,
    recipe_artifact_service,
    recipe_candidate_approval_service,
    recipe_candidate_store,
    recipe_generation_run_service,
    recipe_generation_status_service,
    source_execution_readiness_service,
    source_registry_service,
    templates,
)
from job_agent.web.form_options import recipe_options
from job_agent.web.view_models.source_status import build_source_page_status, build_source_setup_steps

router = APIRouter()


@router.get("/sources", response_class=HTMLResponse)
def source_overview(
    request: Request,
    message: str = "",
    warning: str = "",
) -> HTMLResponse:
    all_sources = source_registry_service().list_sources()
    sources = [source for source in all_sources if source.status != "archived"]
    archived_sources = [source for source in all_sources if source.status == "archived"]
    execution_by_source = {
        str(source.get("source_id") or ""): source
        for source in execution_source_service().list_sources()
        if isinstance(source, dict)
    }
    source_cards = [
        _source_card_context(source, execution_by_source.get(source.id))
        for source in sources
    ]
    archived_source_cards = [
        _source_card_context(source, execution_by_source.get(source.id))
        for source in archived_sources
    ]
    return templates.TemplateResponse(
        request,
        "sources.html",
        {
            "request": request,
            "sources": sources,
            "archived_sources": archived_sources,
            "source_cards": source_cards,
            "archived_source_cards": archived_source_cards,
            "message": message,
            "warning": warning,
            "execution_by_source": execution_by_source,
            "daily_run_enabled_count": sum(
                1 for source in execution_by_source.values() if bool(source.get("enabled", True))
            ),
        },
    )


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
            "name": name,
            "url": url,
            "recipe_path": recipe_path,
            "notes": notes,
            "warning": warning,
            "recipe_options": recipe_options(execution_source_service().root),
        },
    )


@router.post("/sources/new", response_class=HTMLResponse)
def create_source(
    request: Request,
    name: str = Form(""),
    url: str = Form(""),
    recipe_path: str = Form(""),
    notes: str = Form(""),
):
    try:
        created = source_registry_service().add_source(
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
                "name": name,
                "url": url,
                "recipe_path": recipe_path,
                "notes": notes,
                "warning": str(exc),
                "recipe_options": recipe_options(execution_source_service().root),
            },
            status_code=400,
        )
    message = "Source added. It is saved only for setup and is not included in the daily run yet."
    if created.recipe_path:
        message += " Review the reading plan next."
    else:
        message += " Teach the app how to read it next."
    return _redirect_to_source(created.id, message=message)


@router.get("/sources/{source_id}", response_class=HTMLResponse)
def source_detail(request: Request, source_id: str, message: str = "", warning: str = "") -> HTMLResponse:
    source = source_registry_service().get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found.")
    execution_entry = execution_source_service().find_by_source_id(source.id)
    artifacts = recipe_artifact_service().list_artifacts_for_source(source)
    recipe_candidates = _candidates_for_source(source, artifacts)
    generation_status = recipe_generation_status_service().build_for_source(source.id)
    go_live_readiness = source_execution_readiness_service().evaluate(source.id)
    best_artifact_dir = generation_status.best_artifact.artifact_dir if generation_status.best_artifact else ""
    recipe_explanation = explain_recipe(source.recipe_path, root=execution_source_service().root) if source.recipe_path else None
    recipe_preview_auto_url = _recipe_preview_url(source, auto_run=True)
    source_status = build_source_page_status(
        source,
        execution_entry,
        go_live_readiness,
        recipe_preview_url=recipe_preview_auto_url,
        generation_status=generation_status,
    )
    source_setup_steps = build_source_setup_steps(
        source,
        execution_entry,
        go_live_readiness,
        generation_status,
        recipe_preview_url=recipe_preview_auto_url,
    )
    response = templates.TemplateResponse(
        request,
        "source_detail.html",
        {
            "request": request,
            "source": source,
            "recipe_preview_url": _recipe_preview_url(source),
            "execution_entry": execution_entry,
            "execution_message": message,
            "execution_warning": warning,
            "recipe_artifacts": artifacts,
            "recipe_candidates": recipe_candidates,
            "recipe_generation_status": generation_status,
            "recipe_explanation": recipe_explanation,
            "recipe_capabilities": _recipe_capabilities(recipe_explanation),
            "source_status": source_status,
            "source_setup_steps": source_setup_steps,
            "go_live_readiness": go_live_readiness,
            "recipe_options": recipe_options(execution_source_service().root),
            "kind_options": SOURCE_KIND_DEFINITIONS,
            "status_options": SOURCE_STATUS_DEFINITIONS,
            "compatibility_url": _compatibility_url(source),
            "recipe_editor_url": _recipe_editor_url(source, best_artifact_dir),
            "recipe_preview_auto_url": recipe_preview_auto_url,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


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
        updated = source_registry_service().update_source(
            source_id,
            name=name,
            kind=kind,
            url=url,
            status=status,
            recipe_path=recipe_path,
            notes=notes,
        )
        if updated.status == "archived":
            _disable_execution_entry_if_present(updated.id)
    except (KeyError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Source update failed: {exc}")
    return _redirect_to_source(source_id, message="Source registry updated.")


@router.post("/sources/{source_id}/archive")
def archive_registry_source(source_id: str) -> RedirectResponse:
    try:
        source = source_registry_service().archive_source(source_id)
        disabled = _disable_execution_entry_if_present(source.id)
    except KeyError as exc:
        return _redirect_to_sources(warning=f"Source archive failed: {exc}")
    suffix = " Existing daily-run execution entry was disabled." if disabled else ""
    return _redirect_to_sources(
        message=f"Source archived. It is hidden from normal source lists and blocked from daily-run enablement.{suffix}",
    )


@router.post("/sources/{source_id}/restore")
def restore_registry_source(source_id: str) -> RedirectResponse:
    try:
        source_registry_service().restore_source(source_id)
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
    source = _registry_source_or_404(source_id)
    if not source.url:
        return _redirect_to_source(source_id, warning="Save a source URL before capturing calibration evidence.")
    bounded_candidates = max(5, min(max_candidates, 50))
    root = execution_source_service().root
    try:
        result = capture_recipe_calibration(
            source.url,
            recipe_path=source.recipe_path or None,
            rendered=True if rendered else None,
            root=root,
            max_candidates=bounded_candidates,
            capture_detail=bool(capture_detail),
        )
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Calibration capture failed: {exc}")

    try:
        artifact_label = result.artifact_dir.relative_to(root).as_posix()
    except ValueError:
        artifact_label = result.artifact_dir.as_posix()
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
    rendered: str = Form("1"),
    capture_detail: str = Form("1"),
    max_candidates: int = Form(30),
    refine: str = Form("1"),
    max_attempts: int = Form(3),
) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_not_archived(source)
    if not source.url:
        return _redirect_to_source(source_id, warning="Save a source URL before teaching the app how to read it.")
    bounded_candidates = max(5, min(max_candidates, 50))
    try:
        run = recipe_generation_run_service().start_from_source_capture(
            source_id,
            rendered=bool(rendered),
            capture_detail=bool(capture_detail),
            max_candidates=bounded_candidates,
            refine=bool(refine),
            max_attempts=max_attempts,
        )
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Could not learn this source yet: {exc}")
    return RedirectResponse(f"/sources/{source_id}/recipe-generation/{run['run_id']}", status_code=303)


@router.post("/sources/{source_id}/recipe/update")
def update_source_recipe(
    source_id: str,
    recipe_path: str = Form(""),
) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    normalized_recipe_path = recipe_path.replace("\\", "/").strip()
    kind = "recipe" if normalized_recipe_path else ("job_board" if source.kind == "recipe" else source.kind)
    try:
        source_registry_service().update_source(
            source_id,
            name=source.name,
            kind=kind,
            url=source.url,
            status=source.status,
            recipe_path=normalized_recipe_path,
            notes=source.notes,
        )
    except (KeyError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Recipe update failed: {exc}")
    return _redirect_to_source(source_id, message="Source recipe updated.")


@router.post("/sources/{source_id}/execution/create")
def create_execution_source(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_recipe_source(source)
    _require_not_archived(source)
    execution_source_service().create_or_update_recipe_source(source, enabled=False)
    return _redirect_to_source(
        source_id,
        message=(
            "Disabled daily-run entry prepared in sources/recruiting-sites.yaml. "
            "It will not run until you explicitly enable it after readiness checks."
        ),
    )


@router.post("/sources/{source_id}/execution/update")
def update_execution_source(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_recipe_source(source)
    _require_not_archived(source)
    execution_source_service().create_or_update_recipe_source(source, enabled=False)
    return _redirect_to_source(source_id, message="Execution entry updated and kept disabled.")


@router.post("/sources/{source_id}/execution/enable")
def enable_execution_source(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_recipe_source(source)
    _require_not_archived(source)
    result = source_execution_readiness_service().enable_when_ready(source.id)
    if not result.enabled:
        warning = " ".join(result.check.blockers[:3]) or "Source is not ready for daily-run enablement."
        return _redirect_to_source(source_id, warning=warning)
    return _redirect_to_source(source_id, message="Execution entry enabled for daily runs.")


@router.post("/sources/{source_id}/execution/disable")
def disable_execution_source(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    try:
        execution_source_service().disable(source.id)
    except KeyError:
        return _redirect_to_source(source_id, warning="No execution entry exists to disable.")
    return _redirect_to_source(source_id, message="Execution entry disabled.")


@router.get("/sources/{source_id}/dry-run", response_class=HTMLResponse)
def source_dry_run(request: Request, source_id: str, force_disabled: bool = False) -> HTMLResponse:
    source = _registry_source_or_404(source_id)
    execution_entry = execution_source_service().find_by_source_id(source.id)
    result = SourceDryRunService(execution_source_service().root).dry_run(
        source.id,
        force_disabled=force_disabled,
    )
    return templates.TemplateResponse(
        request,
        "source_dry_run.html",
        {
            "request": request,
            "source": source,
            "execution_entry": execution_entry,
            "result": result,
            "force_disabled": force_disabled,
        },
    )


@router.get("/sources/{source_id}/test-run", response_class=HTMLResponse)
def source_test_run(request: Request, source_id: str) -> HTMLResponse:
    source = _registry_source_or_404(source_id)
    _ensure_disabled_execution_entry(source)
    execution_entry = execution_source_service().find_by_source_id(source.id)
    response = templates.TemplateResponse(
        request,
        "source_test_run.html",
        {
            "request": request,
            "source": source,
            "execution_entry": execution_entry,
            "force_disabled": bool(execution_entry and not bool(execution_entry.get("enabled", True))),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/sources/{source_id}/test-run")
def run_source_test(source_id: str) -> JSONResponse:
    source = _registry_source_or_404(source_id)
    _ensure_disabled_execution_entry(source)
    execution_entry = execution_source_service().find_by_source_id(source.id)
    force_disabled = bool(execution_entry and not bool(execution_entry.get("enabled", True)))
    result = SourceDryRunService(execution_source_service().root).dry_run(
        source.id,
        force_disabled=force_disabled,
    )
    readiness = source_execution_readiness_service().save_from_dry_run(result)
    return JSONResponse(_source_test_payload(source, result, readiness))


@router.post("/sources/{source_id}/test-run/stream")
def run_source_test_stream(source_id: str) -> StreamingResponse:
    source = _registry_source_or_404(source_id)
    _ensure_disabled_execution_entry(source)
    execution_entry = execution_source_service().find_by_source_id(source.id)
    force_disabled = bool(execution_entry and not bool(execution_entry.get("enabled", True)))

    def stream_events():
        events: queue.Queue[dict | None] = queue.Queue()

        def progress(event: dict) -> None:
            events.put({"type": "progress", "event": event})

        def worker() -> None:
            try:
                result = SourceDryRunService(execution_source_service().root).dry_run(
                    source.id,
                    force_disabled=force_disabled,
                    progress_callback=progress,
                )
                readiness = source_execution_readiness_service().save_from_dry_run(result)
                events.put({"type": "complete", "data": _source_test_payload(source, result, readiness)})
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


@router.post("/sources/{source_id}/dry-run-readiness")
def dry_run_source_readiness(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    execution_entry = execution_source_service().find_by_source_id(source.id)
    force_disabled = bool(execution_entry and not bool(execution_entry.get("enabled", True)))
    result = SourceDryRunService(execution_source_service().root).dry_run(
        source.id,
        force_disabled=force_disabled,
    )
    readiness = source_execution_readiness_service().save_from_dry_run(result)
    message = f"Dry-run readiness saved: {readiness.readiness_status}. {readiness.dry_run_job_count} jobs extracted."
    if readiness.readiness_status == "blocked":
        return _redirect_to_source(source_id, warning=message)
    return _redirect_to_source(source_id, message=message)


@router.post("/sources/{source_id}/enable-when-ready")
def enable_source_when_ready(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_recipe_source(source)
    _require_not_archived(source)
    result = source_execution_readiness_service().enable_when_ready(source.id)
    if not result.enabled:
        warning = " ".join(result.check.blockers[:3]) or "Source is not ready for daily-run enablement."
        return _redirect_to_source(source_id, warning=warning)
    return _redirect_to_source(source_id, message="Execution entry enabled after go-live readiness checks passed.")


@router.post("/sources/{source_id}/run-now")
def run_source_now(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
    _require_not_archived(source)
    execution_entry = execution_source_service().find_by_source_id(source.id)
    if not execution_entry:
        return _redirect_to_source(source_id, warning="Create an execution entry before running this source.")
    if not bool(execution_entry.get("enabled", True)):
        return _redirect_to_source(source_id, warning="Enable this source before running.")
    result = SingleSourceRunService(execution_source_service().root).run(source.id)
    if result.run_detail_url:
        return RedirectResponse(result.run_detail_url, status_code=303)
    return _redirect_to_source(
        source_id,
        warning=f"Single-source run did not start: {result.status}.",
    )


@router.post("/sources/{source_id}/recipe-candidates/generate")
def generate_recipe_candidate(
    source_id: str,
    artifact_dir: str = Form(...),
    refine: str = Form(""),
    max_attempts: int = Form(3),
) -> RedirectResponse:
    try:
        run = recipe_generation_run_service().start(
            source_id,
            artifact_dir=artifact_dir,
            refine=bool(refine),
            max_attempts=max_attempts,
        )
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Recipe candidate generation failed: {exc}")
    return RedirectResponse(f"/sources/{source_id}/recipe-generation/{run['run_id']}", status_code=303)


@router.get("/sources/{source_id}/recipe-generation/{run_id}", response_class=HTMLResponse)
def recipe_generation_run_detail(request: Request, source_id: str, run_id: str) -> HTMLResponse:
    source = _registry_source_or_404(source_id)
    try:
        run = recipe_generation_run_service().load(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if run.get("source_id") != source.id:
        raise HTTPException(status_code=404, detail="Recipe generation run does not belong to this source.")
    response = templates.TemplateResponse(
        request,
        "recipe_generation_run.html",
        {
            "request": request,
            "source": source,
            "run": run,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/sources/{source_id}/recipe-generation/{run_id}/status")
def recipe_generation_run_status(source_id: str, run_id: str) -> JSONResponse:
    source = _registry_source_or_404(source_id)
    try:
        run = recipe_generation_run_service().load(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if run.get("source_id") != source.id:
        raise HTTPException(status_code=404, detail="Recipe generation run does not belong to this source.")
    return JSONResponse(run)


@router.get("/recipe-candidates/{candidate_id}", response_class=HTMLResponse)
def recipe_candidate_detail(request: Request, candidate_id: str, source_id: str = "") -> HTMLResponse:
    try:
        candidate = recipe_candidate_store().load_candidate(candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    source = source_registry_service().get_source(source_id) if source_id else _source_for_candidate(candidate)
    approval_recipe_path = recipe_candidate_approval_service().suggested_recipe_path(candidate, source)
    candidate_can_be_used = (
        candidate.status == "pending"
        and bool(candidate.suggested_recipe_yaml.strip())
        and bool(candidate.schema_valid)
        and candidate.quality_status != "poor"
        and (not candidate.refinement_used or candidate.refinement_accepted)
    )
    return templates.TemplateResponse(
        request,
        "recipe_candidate_detail.html",
        {
            "request": request,
            "candidate": candidate,
            "source": source,
            "approval_recipe_path": approval_recipe_path,
            "candidate_can_be_used": candidate_can_be_used,
        },
    )


@router.post("/recipe-candidates/{candidate_id}/reject")
def reject_recipe_candidate(candidate_id: str, source_id: str = Form(""), reason: str = Form("")) -> RedirectResponse:
    try:
        recipe_candidate_store().reject_candidate(candidate_id, reason=reason)
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
) -> RedirectResponse:
    try:
        result = approved_recipe_adoption_service().adopt(
            candidate_id,
            source_id,
            prepare_disabled_execution_entry=bool(prepare_disabled_execution_entry),
        )
    except ValueError as exc:
        return _redirect_to_source(source_id, warning=f"Approved recipe adoption failed: {exc}")
    parts = [f"Approved recipe adopted for {result.source_name}."]
    if result.execution_entry_created:
        parts.append("Disabled execution entry created.")
    if result.execution_entry_updated:
        parts.append("Disabled execution entry updated.")
    return _redirect_to_source(source_id, message=" ".join(parts))


@router.post("/recipe-candidates/{candidate_id}/approve")
def approve_recipe_candidate(
    candidate_id: str,
    recipe_path: str = Form(...),
    source_id: str = Form(""),
    overwrite: str = Form(""),
) -> RedirectResponse:
    source = source_registry_service().get_source(source_id) if source_id else None
    try:
        result = recipe_candidate_approval_service().approve(
            candidate_id,
            recipe_path,
            source_id=source_id,
            overwrite=bool(overwrite),
            base_url=source.url if source else "",
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
            adoption = approved_recipe_adoption_service().adopt(
                candidate_id,
                source_id,
                prepare_disabled_execution_entry=False,
            )
        except ValueError as exc:
            return _redirect_to_source(source_id, warning=f"Reading plan was saved, but could not be selected: {exc}")
        return _redirect_to_source(
            source_id,
            message=(
                f"Reading plan saved and selected for {adoption.source_name}. "
                f"Review extracted {result.preview.extracted_job_count if result.preview else 0} jobs."
            ),
        )
    return RedirectResponse(f"/recipe-candidates/{candidate_id}", status_code=303)


def _recipe_preview_url(source, *, auto_run: bool = False) -> str:
    if not source.recipe_path:
        return ""
    params = {
        "recipe_path": source.recipe_path,
        "input_path_or_url": source.url,
        "source_mode": "configured",
        "selected_source_id": source.id,
    }
    if auto_run:
        params["auto_run"] = "1"
    return f"/recipe-preview?{urlencode(params)}"


def _compatibility_url(source) -> str:
    params = {
        "source_mode": "configured",
        "selected_source_id": source.id,
        "url": source.url,
        "recipe_path": source.recipe_path,
        "show_saved": "1",
    }
    return f"/compatibility?{urlencode(params)}"


def _recipe_editor_url(source, artifact_dir: str = "") -> str:
    if not source.recipe_path:
        return "/recipe-editor"
    params = {"recipe_path": source.recipe_path}
    if artifact_dir:
        params["artifact_dir"] = artifact_dir
    return f"/recipe-editor?{urlencode(params)}"


def _source_card_context(source, execution_entry) -> dict:
    readiness = source_execution_readiness_service().evaluate(source.id)
    return {
        "source": source,
        "execution": execution_entry,
        "status": build_source_page_status(
            source,
            execution_entry,
            readiness,
            recipe_preview_url=_recipe_preview_url(source, auto_run=True),
            generation_status=recipe_generation_status_service().build_for_source(source.id),
        ),
    }


def _recipe_capabilities(recipe_explanation) -> list[dict[str, str]]:
    if not recipe_explanation:
        return []
    card_detail = _explanation_detail(recipe_explanation.listing_fields, "Job card") or "configured listing blocks"
    field_labels = [
        item.label
        for item in recipe_explanation.listing_fields
        if item.label not in {"Job card"} and item.detail != "Not configured."
    ]
    detail_status = "Will open detail pages" if recipe_explanation.detail_follow else "Listing page only"
    detail_text = (
        f"Checks sample one posting detail page; full runs follow retained listings with "
        f"{recipe_explanation.detail_delay:g}s delay."
        if recipe_explanation.detail_follow
        else "Does not open posting detail pages."
    )
    pagination_status = "Can follow pagination" if recipe_explanation.pagination_configured else "No pagination"
    pagination_text = (
        f"Can follow page links; full runs may scan up to {recipe_explanation.pagination_max_pages} pages."
        if recipe_explanation.pagination_configured
        else "No pagination selectors are configured."
    )
    return [
        {
            "label": "Find listing cards",
            "status": "Configured",
            "detail": f"Uses {card_detail} to find job tiles on the source page.",
        },
        {
            "label": "Read job fields",
            "status": f"{len(field_labels)} fields",
            "detail": ", ".join(field_labels) if field_labels else "No listing fields are configured.",
        },
        {"label": "Open postings", "status": detail_status, "detail": detail_text},
        {"label": "Handle pagination", "status": pagination_status, "detail": pagination_text},
    ]


def _explanation_detail(items, label: str) -> str:
    for item in items:
        if item.label == label:
            return item.detail
    return ""


def _source_test_payload(source, result, readiness) -> dict[str, object]:
    return {
        "ok": result.status not in {"not_found", "disabled", "failing"},
        "source_id": result.source_id,
        "source_name": result.source_name,
        "source_type": result.source_type,
        "source_enabled": result.source_enabled,
        "forced_disabled": result.forced_disabled,
        "status": result.status,
        "job_count": result.job_count,
        "warning_count": result.warning_count,
        "warnings": result.warnings,
        "jobs": [_dry_run_job_mapping(job) for job in result.jobs],
        "jobs_returned": len(result.jobs),
        "recipe_path": result.recipe_path,
        "recipe_source_name": result.recipe_source_name,
        "base_url": result.base_url,
        "mode_used": result.mode_used,
        "run_steps": result.run_steps,
        "pagination_configured": result.pagination_configured,
        "pagination_link_count": result.pagination_link_count,
        "pagination_max_pages": result.pagination_max_pages,
        "pagination_fetch_count": result.pagination_fetch_count,
        "pagination_fetch_attempts": result.pagination_fetch_attempts,
        "listing_observed_count": result.listing_observed_count,
        "listing_extracted_count": result.listing_extracted_count,
        "listing_missing_url_count": result.listing_missing_url_count,
        "listing_rejected_count": result.listing_rejected_count,
        "listing_duplicate_count": result.listing_duplicate_count,
        "listing_limit_skipped_count": result.listing_limit_skipped_count,
        "listing_pages": result.listing_pages,
        "seen_new_count": result.seen_new_count,
        "seen_changed_count": result.seen_changed_count,
        "seen_previously_seen_count": result.seen_previously_seen_count,
        "count_explanations": result.count_explanations,
        "detail_follow_enabled": result.detail_follow_enabled,
        "detail_fetch_limit": result.detail_fetch_limit,
        "detail_fetch_count": result.detail_fetch_count,
        "detail_enriched_count": result.detail_enriched_count,
        "detail_request_delay_seconds": result.detail_request_delay_seconds,
        "detail_attempts": result.detail_attempts,
        "field_checks": result.field_checks,
        "capability_checks": result.capability_checks,
        "readiness_status": readiness.readiness_status,
        "readiness_summary": readiness.readiness_summary,
        "readiness_blockers": readiness.blockers,
        "readiness_warnings": readiness.warnings,
        "source_url": source.url,
    }


def _dry_run_job_mapping(job) -> dict[str, object]:
    return {
        "title": job.title,
        "url": job.url,
        "source": job.source,
        "source_id": job.source_id,
        "location": job.location,
        "remote": job.remote,
        "rate": job.rate,
        "workload": job.workload,
        "posted_date": job.posted_date,
        "start_date": job.start_date,
        "languages": job.languages,
        "description": job.description,
        "description_preview": job.description_preview,
        "extraction_notes": job.extraction_notes,
    }


def _registry_source_or_404(source_id: str):
    source = source_registry_service().get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found.")
    return source


def _require_recipe_source(source) -> None:
    if not source.recipe_path:
        raise HTTPException(status_code=400, detail="Only recipe-backed sources can be configured for recipe execution.")


def _require_not_archived(source) -> None:
    if source.status == "archived":
        raise HTTPException(status_code=400, detail="Archived sources cannot be prepared, enabled, or run.")


def _ensure_disabled_execution_entry(source) -> None:
    _require_recipe_source(source)
    _require_not_archived(source)
    if execution_source_service().find_by_source_id(source.id):
        return
    execution_source_service().create_or_update_recipe_source(source, enabled=False)


def _disable_execution_entry_if_present(source_id: str) -> bool:
    try:
        execution_source_service().disable(source_id)
    except KeyError:
        return False
    return True


def _redirect_to_source(source_id: str, *, message: str = "", warning: str = "") -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if warning:
        params["warning"] = warning
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/sources/{source_id}{suffix}", status_code=303)


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


def _candidates_for_source(source, artifacts) -> list:
    artifact_dirs = {artifact.artifact_dir for artifact in artifacts}
    result = []
    for summary in recipe_candidate_store().list_candidates():
        try:
            candidate = recipe_candidate_store().load_candidate(summary.candidate_id)
        except ValueError:
            continue
        if _candidate_matches_source(candidate, source, artifact_dirs):
            result.append(candidate)
    return sorted(result, key=lambda item: item.created_at, reverse=True)[:10]


def _source_for_candidate(candidate):
    for source in source_registry_service().list_sources():
        if _candidate_matches_source(candidate, source, set()):
            return source
    return None


def _candidate_matches_source(candidate, source, artifact_dirs: set[str]) -> bool:
    if candidate.source_name.strip().lower() == source.name.strip().lower():
        return True
    if source.url and candidate.start_url and _same_host_path(source.url, candidate.start_url):
        return True
    return bool(candidate.artifact_dir and candidate.artifact_dir in artifact_dirs)


def _same_host_path(left: str, right: str) -> bool:
    from urllib.parse import urlparse

    left_parsed = urlparse(left if "://" in left else f"https://{left}")
    right_parsed = urlparse(right if "://" in right else f"https://{right}")
    left_host = left_parsed.netloc.lower().removeprefix("www.")
    right_host = right_parsed.netloc.lower().removeprefix("www.")
    if not left_host or left_host != right_host:
        return False
    left_path = left_parsed.path.rstrip("/")
    right_path = right_parsed.path.rstrip("/")
    return not left_path or right_path == left_path or right_path.startswith(f"{left_path}/")


def _display_path(path, root) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
