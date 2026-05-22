from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from job_agent.services.recipe_calibration_service import capture_recipe_calibration
from job_agent.services.recipe_suggestion_service import suggest_recipe_from_artifact, suggest_recipe_with_refinement
from job_agent.services.single_source_run_service import SingleSourceRunService
from job_agent.services.source_dry_run_service import SourceDryRunService
from job_agent.services.source_registry_service import VALID_STATUSES
from job_agent.web.dependencies import (
    approved_recipe_adoption_service,
    execution_source_service,
    recipe_artifact_service,
    recipe_candidate_approval_service,
    recipe_candidate_store,
    recipe_generation_status_service,
    source_execution_readiness_service,
    source_registry_service,
    templates,
)
from job_agent.web.form_options import recipe_options

router = APIRouter()
SOURCE_STATUS_OPTIONS = ["active", "experimental", "needs_review", "disabled"]


@router.get("/sources", response_class=HTMLResponse)
def source_overview(request: Request) -> HTMLResponse:
    sources = source_registry_service().list_sources()
    execution_by_source = {
        str(source.get("source_id") or ""): source
        for source in execution_source_service().list_sources()
        if isinstance(source, dict)
    }
    return templates.TemplateResponse(
        request,
        "sources.html",
        {"request": request, "sources": sources, "execution_by_source": execution_by_source},
    )


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
            "recipe_artifacts": artifacts,
            "recipe_candidates": recipe_candidates,
            "recipe_generation_status": generation_status,
            "go_live_readiness": go_live_readiness,
            "recipe_options": recipe_options(execution_source_service().root),
            "status_options": [status for status in SOURCE_STATUS_OPTIONS if status in VALID_STATUSES],
        },
    )


@router.post("/sources/{source_id}/registry/update")
def update_registry_source(
    source_id: str,
    name: str = Form(...),
    url: str = Form(""),
    status: str = Form(...),
    recipe_path: str = Form(""),
    notes: str = Form(""),
) -> RedirectResponse:
    try:
        source_registry_service().update_source(
            source_id,
            name=name,
            url=url,
            status=status,
            recipe_path=recipe_path,
            notes=notes,
        )
    except (KeyError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Source update failed: {exc}")
    return _redirect_to_source(source_id, message="Source registry updated.")


@router.post("/sources/{source_id}/recipe-calibration/capture")
def capture_recipe_calibration_artifact(
    source_id: str,
    rendered: str = Form(""),
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
    return _redirect_to_source(source_id, message=message)


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
    result = source_execution_readiness_service().enable_when_ready(source.id)
    if not result.enabled:
        warning = " ".join(result.check.blockers[:3]) or "Source is not ready for daily-run enablement."
        return _redirect_to_source(source_id, warning=warning)
    return _redirect_to_source(source_id, message="Execution entry enabled after go-live readiness checks passed.")


@router.post("/sources/{source_id}/run-now")
def run_source_now(source_id: str) -> RedirectResponse:
    source = _registry_source_or_404(source_id)
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
    source = _registry_source_or_404(source_id)
    try:
        artifact_path = recipe_artifact_service().resolve_artifact_path(artifact_dir)
        if bool(refine):
            refinement = suggest_recipe_with_refinement(
                artifact_path,
                source_name=source.name,
                start_url=source.url,
                max_attempts=max_attempts,
                root=execution_source_service().root,
            )
            candidate = recipe_candidate_store().save_candidate_from_refinement(refinement)
        else:
            suggestion = suggest_recipe_from_artifact(
                artifact_path,
                source_name=source.name,
                start_url=source.url,
                root=execution_source_service().root,
            )
            candidate = recipe_candidate_store().save_candidate_from_suggestion(suggestion)
    except (RuntimeError, ValueError) as exc:
        return _redirect_to_source(source_id, warning=f"Recipe candidate generation failed: {exc}")
    return _redirect_to_source(source_id, message=f"Pending recipe candidate saved: {candidate.candidate_id}")


@router.get("/recipe-candidates/{candidate_id}", response_class=HTMLResponse)
def recipe_candidate_detail(request: Request, candidate_id: str, source_id: str = "") -> HTMLResponse:
    try:
        candidate = recipe_candidate_store().load_candidate(candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    source = source_registry_service().get_source(source_id) if source_id else _source_for_candidate(candidate)
    approval_recipe_path = recipe_candidate_approval_service().suggested_recipe_path(candidate, source)
    return templates.TemplateResponse(
        request,
        "recipe_candidate_detail.html",
        {
            "request": request,
            "candidate": candidate,
            "source": source,
            "approval_recipe_path": approval_recipe_path,
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
        return _redirect_to_source(
            source_id,
            message=(
                f"Recipe candidate approved: {result.candidate.candidate_id}. "
                f"Preview extracted {result.preview.extracted_job_count if result.preview else 0} jobs."
            ),
        )
    return RedirectResponse(f"/recipe-candidates/{candidate_id}", status_code=303)


def _recipe_preview_url(source) -> str:
    if not source.recipe_path:
        return ""
    params = {
        "recipe_path": source.recipe_path,
        "input_path_or_url": source.url,
        "source_mode": "configured",
        "selected_source_id": source.id,
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
