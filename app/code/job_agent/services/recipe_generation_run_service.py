from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from job_agent.config import ROOT
from job_agent.io.atomic import atomic_write_text
from job_agent.io.json_store import read_json, write_json
from job_agent.paths import display_path, output_dir, resolve_project_path
from job_agent.services.recipe_artifact_service import RecipeArtifactService
from job_agent.services.recipe_calibration_service import capture_recipe_calibration
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_suggestion_service import (
    RecipeRefinementResult,
    RecipeSuggestionResult,
    load_recipe_suggestion_evidence,
    suggest_recipe_from_artifact,
    suggest_recipe_with_refinement,
)
from job_agent.services.source_access_gate_service import SourceAccessGateService
from job_agent.services.source_registry_service import SourceRegistryService

_EXECUTOR = ThreadPoolExecutor(max_workers=1)
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_RUN_FILE_LOCK = threading.RLock()


class RecipeGenerationRunService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.runs_dir = output_dir(self.root) / "recipe-generation-runs"

    def start(
        self,
        source_id: str,
        *,
        artifact_dir: str,
        refine: bool = True,
        max_attempts: int = 3,
        run_async: bool = True,
        llm_model: str = "",
    ) -> dict[str, Any]:
        source = SourceRegistryService(self.root).get_source(source_id)
        if not source:
            raise ValueError(f"Source not found: {source_id}")
        artifact_path = RecipeArtifactService(self.root).resolve_artifact_path(artifact_dir)
        bounded_attempts = max(1, min(int(max_attempts or 1), 8))
        relative_artifact = _display_path(artifact_path, self.root)
        run = self._initial_run(
            source,
            artifact_dir=relative_artifact,
            refine=bool(refine),
            max_attempts=bounded_attempts,
            llm_model=llm_model,
        )
        return self._queue_run(run, run_async=run_async)

    def start_from_source_capture(
        self,
        source_id: str,
        *,
        rendered: bool | None = None,
        capture_detail: bool = True,
        max_candidates: int = 30,
        refine: bool = True,
        max_attempts: int = 3,
        source_test_insight: dict[str, Any] | None = None,
        run_async: bool = True,
        llm_model: str = "",
    ) -> dict[str, Any]:
        source = SourceRegistryService(self.root).get_source(source_id)
        if not source:
            raise ValueError(f"Source not found: {source_id}")
        if not source.url:
            raise ValueError("Source URL is required before learning a source.")
        bounded_attempts = max(1, min(int(max_attempts or 1), 8))
        bounded_candidates = max(5, min(int(max_candidates or 30), 50))
        capture_rendered = rendered
        if capture_rendered is None and _source_test_insight_prefers_rendered_capture(source_test_insight or {}):
            capture_rendered = True
        run = self._initial_run(
            source,
            artifact_dir="Pending live capture",
            refine=bool(refine),
            max_attempts=bounded_attempts,
            capture_source=True,
            capture_rendered=capture_rendered,
            capture_detail=bool(capture_detail),
            max_candidates=bounded_candidates,
            source_test_insight=source_test_insight or {},
            llm_model=llm_model,
        )
        return self._queue_run(run, run_async=run_async)

    def load(self, run_id: str) -> dict[str, Any]:
        path = self._run_file(run_id)
        if not path.exists():
            raise ValueError(f"Recipe generation run not found: {run_id}")
        with _RUN_FILE_LOCK:
            for attempt in range(5):
                try:
                    data = read_json(path, {}, strict=True)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.02)
        if not isinstance(data, dict):
            raise ValueError(f"Recipe generation run is invalid: {run_id}")
        return data

    def retry(
        self,
        source_id: str,
        run_id: str,
        *,
        run_async: bool = True,
        llm_model: str = "",
    ) -> dict[str, Any]:
        run = self.load(run_id)
        if run.get("source_id") != source_id:
            raise ValueError("Recipe generation run does not belong to this source.")
        if str(run.get("status") or "") in {"pending", "running"}:
            raise ValueError("Recipe generation is still running.")
        selected_model = llm_model or str(run.get("llm_model") or "")
        if run.get("capture_source"):
            source_test_insight = run.get("source_test_insight")
            return self.start_from_source_capture(
                source_id,
                rendered=_capture_rendered_value(run.get("capture_rendered")),
                capture_detail=bool(run.get("capture_detail")),
                max_candidates=int(run.get("max_candidates") or 30),
                refine=bool(run.get("refine")),
                max_attempts=int(run.get("max_attempts") or 3),
                source_test_insight=source_test_insight if isinstance(source_test_insight, dict) else {},
                run_async=run_async,
                llm_model=selected_model,
            )
        return self.start(
            source_id,
            artifact_dir=str(run.get("artifact_dir") or ""),
            refine=bool(run.get("refine")),
            max_attempts=int(run.get("max_attempts") or 3),
            run_async=run_async,
            llm_model=selected_model,
        )

    def _run_generation(self, run_id: str) -> None:
        try:
            run = self._set_run_status(run_id, "running", started_at=_now())
            source = SourceRegistryService(self.root).get_source(str(run["source_id"]))
            if not source:
                raise ValueError(f"Source not found: {run['source_id']}")
            learner_access_evidence: dict[str, Any] = {}
            if run.get("capture_source"):
                access_decision = SourceAccessGateService(self.root).evaluate_source(source, purpose="learn")
                if not access_decision.can_execute:
                    self._append_step(run_id, "Resolve source and capture", "failed", access_decision.message)
                    raise RuntimeError(access_decision.message)
                learner_access_evidence = _learner_access_evidence(source, access_decision)
                session_state_path = (
                    resolve_project_path(self.root, access_decision.session_state_path)
                    if access_decision.session_state_path
                    else None
                )
                self._update_run(
                    run_id,
                    source_session_used=bool(access_decision.uses_session),
                    source_session_scope=access_decision.session_scope if access_decision.uses_session else "",
                    learner_access_evidence=learner_access_evidence,
                )
                session_note = (
                    f" using a connected source session for {access_decision.session_scope or source.name}"
                    if access_decision.uses_session
                    else ""
                )
                self._append_step(
                    run_id,
                    "Resolve source and capture",
                    "running",
                    (
                        f"Opening {source.name} at {source.url} with "
                        f"{_capture_mode_label(run.get('capture_rendered'))}{session_note}."
                    ),
                )
                capture = capture_recipe_calibration(
                    source.url,
                    recipe_path=source.recipe_path or None,
                    rendered=_capture_rendered_value(run.get("capture_rendered")),
                    root=self.root,
                    max_candidates=int(run.get("max_candidates") or 30),
                    capture_detail=bool(run.get("capture_detail")),
                    session_state_path=session_state_path,
                    source_session_scope=access_decision.session_scope if access_decision.uses_session else "",
                )
                artifact_path = capture.artifact_dir
                relative_artifact = _display_path(artifact_path, self.root)
                existing_warnings = list(run.get("warnings") or [])
                self._update_run(
                    run_id,
                    artifact_dir=relative_artifact,
                    warnings=existing_warnings + list(capture.warnings),
                )
                detail = (
                    f"Captured {relative_artifact}: {capture.candidate_count} candidate region(s); "
                    f"recipe extracted {capture.recipe_extracted_count} job(s)."
                )
                if capture.detail_sample_url:
                    detail += f" Detail sample: {capture.detail_sample_url}."
                self._append_step(run_id, "Resolve source and capture", "complete", detail)
            else:
                artifact_path = RecipeArtifactService(self.root).resolve_artifact_path(str(run["artifact_dir"]))
                self._append_step(
                    run_id,
                    "Resolve source and capture",
                    "complete",
                    f"Using {source.name} at {source.url or 'no saved URL'} and saved capture {run['artifact_dir']}.",
                )

            self._append_step(
                run_id,
                "Inspect capture evidence",
                "running",
                "Reading selector report, visible text, pagination evidence, and any captured detail page.",
            )
            source_test_insight = (
                run.get("source_test_insight") if isinstance(run.get("source_test_insight"), dict) else {}
            )
            evidence = load_recipe_suggestion_evidence(
                artifact_path,
                source_name=source.name,
                start_url=source.url,
                existing_recipe_path=resolve_project_path(self.root, source.recipe_path)
                if source.recipe_path
                else None,
                source_test_insight=source_test_insight,
            )
            observations = _evidence_observations(evidence.prompt_payload)
            learner_detail_evidence = _learner_detail_evidence(run_id, artifact_path, observations, self.root)
            self._update_run(
                run_id,
                evidence_summary=evidence.evidence_summary,
                referenced_artifact_files=evidence.referenced_artifact_files,
                warnings=list(evidence.warnings),
                evidence_observations=observations,
                learner_detail_evidence=learner_detail_evidence,
            )
            self._append_step(
                run_id,
                "Inspect capture evidence",
                "complete",
                _observation_summary(observations),
            )

            if bool(run.get("refine")):
                self._append_step(
                    run_id,
                    "Generate and refine reading plan",
                    "running",
                    (
                        "Building selector-based rules, validating them against the saved page, "
                        f"and allowing up to {run['max_attempts']} refinement attempt(s)."
                    ),
                )
                refinement = suggest_recipe_with_refinement(
                    artifact_path,
                    source_name=source.name,
                    start_url=source.url,
                    existing_recipe_path=resolve_project_path(self.root, source.recipe_path)
                    if source.recipe_path
                    else None,
                    source_test_insight=source_test_insight,
                    max_attempts=int(run["max_attempts"]),
                    root=self.root,
                    llm_model=str(run.get("llm_model") or ""),
                )
                result = refinement.final_result
                candidate_store = RecipeCandidateStore(self.root)
                candidate = candidate_store.save_candidate_from_refinement(refinement)
                candidate = candidate_store.update_candidate_evidence(
                    candidate.candidate_id,
                    learner_access_evidence=learner_access_evidence,
                    learner_detail_evidence=learner_detail_evidence,
                )
                self._append_step(
                    run_id,
                    "Generate and refine reading plan",
                    "complete" if refinement.accepted else "warning",
                    _refinement_summary(refinement),
                )
            else:
                self._append_step(
                    run_id,
                    "Generate reading plan",
                    "running",
                    "Creating one reading plan from the saved capture without iterative local refinement.",
                )
                result = suggest_recipe_from_artifact(
                    artifact_path,
                    source_name=source.name,
                    start_url=source.url,
                    existing_recipe_path=resolve_project_path(self.root, source.recipe_path)
                    if source.recipe_path
                    else None,
                    source_test_insight=source_test_insight,
                    root=self.root,
                    llm_model=str(run.get("llm_model") or ""),
                )
                candidate_store = RecipeCandidateStore(self.root)
                candidate = candidate_store.save_candidate_from_suggestion(result)
                candidate = candidate_store.update_candidate_evidence(
                    candidate.candidate_id,
                    learner_access_evidence=learner_access_evidence,
                    learner_detail_evidence=learner_detail_evidence,
                )
                self._append_step(
                    run_id,
                    "Generate reading plan",
                    "complete" if result.schema_valid else "warning",
                    _suggestion_summary(result),
                )

            candidate_path = RecipeCandidateStore(self.root).candidate_path(candidate.candidate_id)
            generated_recipe_path = ""
            ready_for_checks = (
                result.schema_valid
                and result.suggested_recipe_yaml.strip()
                and (not bool(run.get("refine")) or bool(refinement and refinement.accepted))
            )
            if ready_for_checks:
                generated_recipe_path = self._write_generated_recipe(run_id, result.suggested_recipe_yaml)
                save_detail = f"Saved generated plan {candidate.candidate_id} and a temporary recipe file for checks."
            else:
                save_detail = (
                    f"Saved generated plan {candidate.candidate_id}. No temporary recipe file was created because "
                    "the generated plan is not ready to execute."
                )
            self._append_step(run_id, "Save reading plan result", "complete", save_detail)
            payload = _result_payload(
                run_id=run_id,
                source_id=source.id,
                source_name=source.name,
                source_url=source.url,
                source_recipe_path=source.recipe_path,
                result=result,
                candidate_id=candidate.candidate_id,
                candidate_path=_display_path(candidate_path, self.root),
                generated_recipe_path=generated_recipe_path,
                refinement=refinement if bool(run.get("refine")) else None,
            )
            self._update_run(run_id, **payload)
            self._append_step(
                run_id,
                "Prepare next checks",
                "complete",
                "Compatibility evidence and source-test actions are ready when the generated plan is testable.",
            )
            self._set_run_status(run_id, "completed", finished_at=_now())
        except Exception as exc:
            self._append_step(run_id, "Generation failed", "failed", str(exc))
            self._set_run_status(run_id, "failed", error=str(exc), finished_at=_now())

    def _initial_run(
        self,
        source,
        *,
        artifact_dir: str,
        refine: bool,
        max_attempts: int,
        **extra: Any,
    ) -> dict[str, Any]:
        now = _now()
        run = {
            "run_id": self._new_run_id(source.id),
            "source_id": source.id,
            "source_name": source.name,
            "source_url": source.url,
            "artifact_dir": artifact_dir,
            "refine": refine,
            "max_attempts": max_attempts,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "started_at": "",
            "finished_at": "",
            "steps": [
                {
                    "phase": "Queue reading-plan generation",
                    "status": "pending",
                    "detail": "Waiting for the generator to start.",
                    "created_at": now,
                }
            ],
            "warnings": [],
            "error": "",
            "candidate_id": "",
            "candidate_path": "",
            "candidate_url": "",
            "generated_recipe_path": "",
            "compatibility_url": "",
            "suggested_recipe_yaml": "",
            "explanation": "",
            "confidence": "",
            "selected_strategy": "",
            "assumptions": [],
            "schema_valid": False,
            "validation_errors": [],
            "referenced_artifact_files": [],
            "refinement_used": refine,
            "refinement_accepted": False,
            "attempt_count": 0,
            "attempts": [],
            "quality_status": "",
            "extracted_job_count": 0,
            "useful_titles": 0,
            "generic_labels": 0,
            "unique_urls": 0,
            "average_description_length": 0,
            "quality_warnings": [],
            "evidence_summary": "",
            "evidence_observations": {},
            "learner_access_evidence": {},
            "learner_detail_evidence": {},
            "source_test_insight": {},
            "source_session_used": False,
            "source_session_scope": "",
        }
        run.update(extra)
        return run

    def _queue_run(self, run: dict[str, Any], *, run_async: bool) -> dict[str, Any]:
        self._write_run(run)
        run_id = str(run["run_id"])
        if run_async:
            _EXECUTOR.submit(self._run_generation, run_id)
            return run
        self._run_generation(run_id)
        return self.load(run_id)

    def _write_generated_recipe(self, run_id: str, recipe_yaml: str) -> str:
        path = self.runs_dir / run_id / "suggested-recipe.yaml"
        atomic_write_text(path, recipe_yaml.strip() + "\n", encoding="utf-8")
        return _display_path(path, self.root)

    def _append_step(self, run_id: str, phase: str, status: str, detail: str) -> None:
        run = self.load(run_id)
        steps = list(run.get("steps") or [])
        steps.append(
            {
                "phase": phase,
                "status": status,
                "detail": detail,
                "created_at": _now(),
            }
        )
        run["steps"] = steps
        run["updated_at"] = _now()
        self._write_run(run)

    def _set_run_status(self, run_id: str, status: str, **updates: Any) -> dict[str, Any]:
        return self._update_run(run_id, status=status, **updates)

    def _update_run(self, run_id: str, **updates: Any) -> dict[str, Any]:
        run = self.load(run_id)
        run.update(updates)
        run["updated_at"] = _now()
        self._write_run(run)
        return run

    def _write_run(self, run: dict[str, Any]) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with _RUN_FILE_LOCK:
            write_json(self._run_file(str(run["run_id"])), run)

    def _run_file(self, run_id: str) -> Path:
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError(f"Invalid recipe generation run id: {run_id}")
        return self.runs_dir / f"{run_id}.json"

    def _new_run_id(self, source_id: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", source_id.lower()).strip("-") or "source"
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        return f"{stamp}-{slug}"[:90].strip("-")


def _result_payload(
    *,
    run_id: str,
    source_id: str,
    source_name: str,
    source_url: str,
    source_recipe_path: str,
    result: RecipeSuggestionResult,
    candidate_id: str,
    candidate_path: str,
    generated_recipe_path: str,
    refinement: RecipeRefinementResult | None,
) -> dict[str, Any]:
    params = {}
    if generated_recipe_path:
        params = {
            "source_mode": "configured",
            "selected_source_id": source_id,
            "url": source_url,
            "recipe_path": generated_recipe_path,
        }
    approval_recipe_path = (
        source_recipe_path or f"sources/recipes/experimental/{_path_slug(source_id or source_name)}.yaml"
    )
    attempts = []
    if refinement:
        attempts = [
            {
                "attempt_number": attempt.attempt_number,
                "schema_valid": attempt.schema_valid,
                "quality_status": attempt.quality_status,
                "extracted_job_count": attempt.extracted_job_count,
                "useful_titles": attempt.useful_titles,
                "unique_urls": attempt.unique_urls,
                "average_description_length": attempt.average_description_length,
                "revision_reason": attempt.revision_reason,
                "quality_warnings": list(attempt.quality_warnings),
            }
            for attempt in refinement.attempts
        ]
    latest_attempt = refinement.attempts[-1] if refinement and refinement.attempts else None
    return {
        "candidate_id": candidate_id,
        "candidate_path": candidate_path,
        "candidate_url": f"/recipe-candidates/{candidate_id}?{urlencode({'source_id': source_id})}",
        "candidate_approval_url": f"/recipe-candidates/{candidate_id}/approve",
        "approval_recipe_path": approval_recipe_path,
        "generated_recipe_path": generated_recipe_path,
        "compatibility_url": f"/compatibility?{urlencode(params)}" if generated_recipe_path else "",
        "suggested_recipe_yaml": result.suggested_recipe_yaml,
        "explanation": result.explanation,
        "confidence": result.confidence,
        "selected_strategy": result.selected_strategy,
        "assumptions": list(result.assumptions),
        "schema_valid": result.schema_valid,
        "validation_errors": list(result.validation_errors),
        "referenced_artifact_files": list(result.referenced_artifact_files),
        "refinement_used": bool(refinement),
        "refinement_accepted": bool(refinement.accepted) if refinement else False,
        "attempt_count": len(refinement.attempts) if refinement else 0,
        "attempts": attempts,
        "quality_status": latest_attempt.quality_status if latest_attempt else "",
        "extracted_job_count": latest_attempt.extracted_job_count if latest_attempt else 0,
        "useful_titles": latest_attempt.useful_titles if latest_attempt else 0,
        "generic_labels": latest_attempt.generic_labels if latest_attempt else 0,
        "unique_urls": latest_attempt.unique_urls if latest_attempt else 0,
        "average_description_length": latest_attempt.average_description_length if latest_attempt else 0,
        "quality_warnings": list(latest_attempt.quality_warnings) if latest_attempt else [],
    }


def _evidence_observations(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = payload.get("top_candidates") if isinstance(payload, dict) else []
    pagination = payload.get("observed_pagination_links") if isinstance(payload, dict) else []
    ajax_pagination = payload.get("observed_ajax_pagination_templates") if isinstance(payload, dict) else []
    api_candidates = payload.get("observed_api_candidates") if isinstance(payload, dict) else []
    applications = payload.get("observed_application_entries") if isinstance(payload, dict) else []
    blueprint = payload.get("recipe_blueprint") if isinstance(payload, dict) else {}
    return {
        "top_candidate_count": len(candidates) if isinstance(candidates, list) else 0,
        "pagination_link_count": len(pagination) if isinstance(pagination, list) else 0,
        "ajax_pagination_template_count": len(ajax_pagination) if isinstance(ajax_pagination, list) else 0,
        "api_candidate_count": len(api_candidates) if isinstance(api_candidates, list) else 0,
        "application_entry_count": len(applications) if isinstance(applications, list) else 0,
        "detail_sample_captured": bool(payload.get("detail_sample_captured")) if isinstance(payload, dict) else False,
        "source_session_used": bool(payload.get("source_session_used")) if isinstance(payload, dict) else False,
        "source_session_scope": str(payload.get("source_session_scope") or "") if isinstance(payload, dict) else "",
        "blueprint_present": bool(blueprint) if isinstance(blueprint, dict) else False,
    }


def _learner_access_evidence(source, decision) -> dict[str, Any]:
    return {
        "source_id": str(getattr(source, "id", "") or ""),
        "source_name": str(getattr(source, "name", "") or ""),
        "captured_at": _now(),
        "status": str(getattr(decision, "status", "") or ""),
        "message": str(getattr(decision, "message", "") or ""),
        "can_execute": bool(getattr(decision, "can_execute", False)),
        "session_required": bool(getattr(decision, "session_required", False)),
        "session_usable": bool(getattr(decision, "session_usable", False)),
        "session_verified": bool(getattr(decision, "session_verified", False)),
        "session_used": bool(getattr(decision, "uses_session", False)),
        "session_scope": str(getattr(decision, "session_scope", "") or ""),
        "session_state_path": str(getattr(decision, "session_state_path", "") or ""),
    }


def _learner_detail_evidence(
    run_id: str,
    artifact_path: Path,
    observations: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "artifact_dir": _display_path(artifact_path, root),
        "captured_at": _now(),
        "detail_sample_captured": bool(observations.get("detail_sample_captured")),
        "source_session_used": bool(observations.get("source_session_used")),
        "source_session_scope": str(observations.get("source_session_scope") or ""),
    }


def _observation_summary(observations: dict[str, Any]) -> str:
    parts = [
        f"{observations.get('top_candidate_count', 0)} candidate selector region(s)",
        f"{observations.get('pagination_link_count', 0)} pagination link(s)",
        f"{observations.get('ajax_pagination_template_count', 0)} AJAX pagination template(s)",
        f"{observations.get('api_candidate_count', 0)} page-declared API candidate(s)",
        "detail sample captured" if observations.get("detail_sample_captured") else "no detail sample captured",
    ]
    if observations.get("blueprint_present"):
        parts.append("deterministic recipe blueprint available")
    if observations.get("source_session_used"):
        scope = str(observations.get("source_session_scope") or "source")
        parts.append(f"connected session used for {scope}")
    return "Observed " + ", ".join(parts) + "."


def _refinement_summary(refinement: RecipeRefinementResult) -> str:
    if not refinement.attempts:
        return "Refinement produced a draft without local quality attempts."
    latest = refinement.attempts[-1]
    return (
        f"Attempt {latest.attempt_number} ended with {latest.quality_status}; "
        f"{latest.extracted_job_count} jobs, {latest.useful_titles} useful titles, "
        f"{latest.unique_urls} unique URLs. Accepted: {'yes' if refinement.accepted else 'no'}."
    )


def _suggestion_summary(result: RecipeSuggestionResult) -> str:
    status = "valid" if result.schema_valid else "invalid"
    return f"Generated a {status} reading plan using {result.selected_strategy or 'unknown strategy'}."


def _capture_rendered_value(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"auto", ""}:
        return None
    return text in {"1", "true", "yes", "rendered"}


def _capture_mode_label(value: Any) -> str:
    rendered = _capture_rendered_value(value)
    if rendered is True:
        return "browser rendering"
    if rendered is False:
        return "normal HTML"
    return "automatic static/rendered detection"


def _source_test_insight_prefers_rendered_capture(insight: dict[str, Any]) -> bool:
    if not isinstance(insight, dict) or not insight:
        return False
    if bool(insight.get("pagination_working_with_unique_pages")):
        return False
    if bool(insight.get("pagination_duplicate_postings")):
        return True
    failed = insight.get("failed_capabilities")
    if isinstance(failed, list):
        for item in failed:
            if not isinstance(item, dict) or str(item.get("status") or "") != "fail":
                continue
            capability = str(item.get("capability") or "").strip()
            detail = str(item.get("detail") or "").lower()
            if capability == "browser_click_pagination" and "browser-click pagination" in detail:
                return True
            if capability == "pagination_strategy" and "does not declare browser-click pagination" in detail:
                return True
            if "interactive browser pagination" in detail:
                return True
    interactive_count = _positive_int(insight.get("interactive_pagination_control_count"), 0)
    if not interactive_count:
        return False
    haystack = " ".join(
        [
            str(insight.get("insight_title") or ""),
            str(insight.get("summary") or ""),
            str(insight.get("recommendation") or ""),
        ]
    ).lower()
    return "browser-click pagination" in haystack and (
        "does not use browser-click pagination" in haystack
        or "does not declare browser-click pagination" in haystack
        or "interactive pagination controls were observed" in haystack
    )


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _display_path(path: Path, root: Path) -> str:
    return display_path(root, path)


def _path_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "source"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
