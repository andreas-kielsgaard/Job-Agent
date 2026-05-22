from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from job_agent.config import ROOT
from job_agent.io.atomic import atomic_write_text
from job_agent.io.json_store import read_json, write_json
from job_agent.services.recipe_artifact_service import RecipeArtifactService
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_suggestion_service import (
    RecipeRefinementResult,
    RecipeSuggestionResult,
    load_recipe_suggestion_evidence,
    suggest_recipe_from_artifact,
    suggest_recipe_with_refinement,
)
from job_agent.services.source_registry_service import SourceRegistryService

_EXECUTOR = ThreadPoolExecutor(max_workers=1)
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_RUN_FILE_LOCK = threading.RLock()


class RecipeGenerationRunService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "output" / "recipe-generation-runs"

    def start(
        self,
        source_id: str,
        *,
        artifact_dir: str,
        refine: bool = True,
        max_attempts: int = 3,
        run_async: bool = True,
    ) -> dict[str, Any]:
        source = SourceRegistryService(self.root).get_source(source_id)
        if not source:
            raise ValueError(f"Source not found: {source_id}")
        artifact_path = RecipeArtifactService(self.root).resolve_artifact_path(artifact_dir)
        bounded_attempts = max(1, min(int(max_attempts or 1), 8))
        run_id = self._new_run_id(source.id)
        relative_artifact = _display_path(artifact_path, self.root)
        now = _now()
        run = {
            "run_id": run_id,
            "source_id": source.id,
            "source_name": source.name,
            "source_url": source.url,
            "artifact_dir": relative_artifact,
            "refine": bool(refine),
            "max_attempts": bounded_attempts,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "started_at": "",
            "finished_at": "",
            "steps": [
                {
                    "phase": "Queue recipe generation",
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
            "recipe_review_url": "",
            "recipe_rules_url": "",
            "suggested_recipe_yaml": "",
            "explanation": "",
            "confidence": "",
            "selected_strategy": "",
            "assumptions": [],
            "schema_valid": False,
            "validation_errors": [],
            "referenced_artifact_files": [],
            "refinement_used": bool(refine),
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
        }
        self._write_run(run)
        if run_async:
            _EXECUTOR.submit(self._run_generation, run_id)
            return run
        self._run_generation(run_id)
        return self.load(run_id)

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

    def _run_generation(self, run_id: str) -> None:
        try:
            run = self._set_run_status(run_id, "running", started_at=_now())
            source = SourceRegistryService(self.root).get_source(str(run["source_id"]))
            if not source:
                raise ValueError(f"Source not found: {run['source_id']}")
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
            evidence = load_recipe_suggestion_evidence(
                artifact_path,
                source_name=source.name,
                start_url=source.url,
                existing_recipe_path=(self.root / source.recipe_path) if source.recipe_path else None,
            )
            observations = _evidence_observations(evidence.prompt_payload)
            self._update_run(
                run_id,
                evidence_summary=evidence.evidence_summary,
                referenced_artifact_files=evidence.referenced_artifact_files,
                warnings=list(evidence.warnings),
                evidence_observations=observations,
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
                    "Generate and refine draft recipe",
                    "running",
                    (
                        "Building a selector-based draft, validating it against the saved page, "
                        f"and allowing up to {run['max_attempts']} refinement attempt(s)."
                    ),
                )
                refinement = suggest_recipe_with_refinement(
                    artifact_path,
                    source_name=source.name,
                    start_url=source.url,
                    existing_recipe_path=(self.root / source.recipe_path) if source.recipe_path else None,
                    max_attempts=int(run["max_attempts"]),
                    root=self.root,
                )
                result = refinement.final_result
                candidate = RecipeCandidateStore(self.root).save_candidate_from_refinement(refinement)
                self._append_step(
                    run_id,
                    "Generate and refine draft recipe",
                    "complete" if refinement.accepted else "warning",
                    _refinement_summary(refinement),
                )
            else:
                self._append_step(
                    run_id,
                    "Generate draft recipe",
                    "running",
                    "Creating one draft recipe from the saved capture without iterative local refinement.",
                )
                result = suggest_recipe_from_artifact(
                    artifact_path,
                    source_name=source.name,
                    start_url=source.url,
                    existing_recipe_path=(self.root / source.recipe_path) if source.recipe_path else None,
                    root=self.root,
                )
                candidate = RecipeCandidateStore(self.root).save_candidate_from_suggestion(result)
                self._append_step(
                    run_id,
                    "Generate draft recipe",
                    "complete" if result.schema_valid else "warning",
                    _suggestion_summary(result),
                )

            generated_recipe_path = self._write_generated_recipe(run_id, result.suggested_recipe_yaml)
            candidate_path = RecipeCandidateStore(self.root).candidate_path(candidate.candidate_id)
            self._append_step(
                run_id,
                "Save draft recipe",
                "complete",
                f"Saved pending candidate {candidate.candidate_id} and a temporary recipe file for checks.",
            )
            payload = _result_payload(
                run_id=run_id,
                source_id=source.id,
                source_url=source.url,
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
                "Compatibility check, recipe review, and candidate review links are ready.",
            )
            self._set_run_status(run_id, "completed", finished_at=_now())
        except Exception as exc:
            self._append_step(run_id, "Generation failed", "failed", str(exc))
            self._set_run_status(run_id, "failed", error=str(exc), finished_at=_now())

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
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        return f"{stamp}-{slug}"[:90].strip("-")


def _result_payload(
    *,
    run_id: str,
    source_id: str,
    source_url: str,
    result: RecipeSuggestionResult,
    candidate_id: str,
    candidate_path: str,
    generated_recipe_path: str,
    refinement: RecipeRefinementResult | None,
) -> dict[str, Any]:
    params = {
        "source_mode": "configured",
        "selected_source_id": source_id,
        "url": source_url,
        "recipe_path": generated_recipe_path,
    }
    preview_params = {
        "source_mode": "configured",
        "selected_source_id": source_id,
        "input_path_or_url": source_url,
        "recipe_path": generated_recipe_path,
    }
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
        "generated_recipe_path": generated_recipe_path,
        "compatibility_url": f"/compatibility?{urlencode(params)}",
        "recipe_review_url": f"/recipe-preview?{urlencode({**preview_params, 'tab': 'execute', 'auto_run': '1'})}",
        "recipe_rules_url": f"/recipe-preview?{urlencode({**preview_params, 'tab': 'explain'})}",
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
    applications = payload.get("observed_application_entries") if isinstance(payload, dict) else []
    blueprint = payload.get("recipe_blueprint") if isinstance(payload, dict) else {}
    return {
        "top_candidate_count": len(candidates) if isinstance(candidates, list) else 0,
        "pagination_link_count": len(pagination) if isinstance(pagination, list) else 0,
        "application_entry_count": len(applications) if isinstance(applications, list) else 0,
        "detail_sample_captured": bool(payload.get("detail_sample_captured")) if isinstance(payload, dict) else False,
        "blueprint_present": bool(blueprint) if isinstance(blueprint, dict) else False,
    }


def _observation_summary(observations: dict[str, Any]) -> str:
    parts = [
        f"{observations.get('top_candidate_count', 0)} candidate selector region(s)",
        f"{observations.get('pagination_link_count', 0)} pagination link(s)",
        "detail sample captured" if observations.get("detail_sample_captured") else "no detail sample captured",
    ]
    if observations.get("blueprint_present"):
        parts.append("deterministic recipe blueprint available")
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
    return f"Generated a {status} recipe using {result.selected_strategy or 'unknown strategy'}."


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
