from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from job_agent.config import ROOT
from job_agent.io.json_store import read_json, write_json
from job_agent.llm import LlmService
from job_agent.paths import output_dir
from job_agent.services.approved_recipe_adoption_service import ApprovedRecipeAdoptionService
from job_agent.services.recipe_candidate_approval_service import RecipeCandidateApprovalService
from job_agent.services.recipe_candidate_policy import candidate_is_reviewable
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_generation_run_service import RecipeGenerationRunService
from job_agent.web.source_workflow import SourceWorkflowHandler

_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_ACTIVE_STATUSES = {"pending", "running"}
_RESUMABLE_STATUSES = {"pending", "running", "blocked", "failed"}


class SourceAutoSetupWorkflowHandler:
    """Drive the guarded source setup path without running full ingestion."""

    def __init__(self, root: Path = ROOT, source: SourceWorkflowHandler | None = None) -> None:
        self.root = Path(root)
        self.source = source or SourceWorkflowHandler(self.root)
        self.recipe_runs = RecipeGenerationRunService(self.root)
        self.candidates = RecipeCandidateStore(self.root)
        self.approvals = RecipeCandidateApprovalService(self.root)
        self.adoptions = ApprovedRecipeAdoptionService(self.root)
        self.runs_dir = output_dir(self.root) / "source-auto-setup-runs"

    def is_configured(self) -> bool:
        try:
            return LlmService(self.root).is_configured()
        except (RuntimeError, ValueError):
            return False

    def prepare(self, source_id: str, *, run_id: str = "") -> dict[str, Any]:
        if run_id.strip():
            run = self.load(run_id)
            if run.get("source_id") != source_id:
                raise ValueError("Automatic setup run does not belong to this source.")
            if run.get("status") == "completed":
                return run
            self._validate_source(source_id)
            return self._update_run(
                str(run["run_id"]),
                status="pending",
                finished_at="",
                error_message="",
                stage="Ready to continue",
                message="Automatic setup will continue from the latest saved checkpoint.",
                progress_percent=max(8, int(run.get("progress_percent") or 8)),
            )

        latest = self.latest_for_source(source_id)
        if latest and latest.get("status") in _ACTIVE_STATUSES:
            self._validate_source(source_id)
            return latest
        source = self._validate_source(source_id)
        return self._create_run(source.id, source.name)

    def run(self, run_id: str, *, progress_callback=None) -> dict[str, Any]:
        run = self.load(run_id)
        if run.get("status") == "completed":
            return run
        if not self.is_configured():
            return self._finish(
                run_id,
                status="failed",
                stage="API key required",
                message="Automatic setup needs an Anthropic API key before it can learn sources.",
                error_message="ANTHROPIC_API_KEY is missing or placeholder.",
                progress_percent=100,
                progress_callback=progress_callback,
            )

        try:
            source_id = str(run.get("source_id") or "")
            source = self._validate_source(source_id)
            run = self._update_run(
                run_id,
                status="running",
                started_at=str(run.get("started_at") or _now()),
                finished_at="",
                error_message="",
                stage="Checking source",
                message=f"Preparing automatic setup for {source.name}.",
                progress_percent=max(10, int(run.get("progress_percent") or 10)),
            )
            self._notify(progress_callback, run)

            if not source.recipe_path:
                run = self._generate_and_adopt_recipe(
                    run_id,
                    source,
                    source_test_insight={},
                    progress_callback=progress_callback,
                )
                source = self.source.require_source(source.id)
            else:
                self._append_event(
                    run_id,
                    "Reading plan selected",
                    f"Using the selected reading plan {source.recipe_path}.",
                )

            run = self._update_run(
                run_id,
                stage="Preparing safe test",
                message="Creating or refreshing the disabled daily-run projection.",
                progress_percent=max(35, int(run.get("progress_percent") or 35)),
            )
            self._notify(progress_callback, run)
            self.source.create_or_update_execution_source(source.id)

            while True:
                run = self._run_source_test(run_id, source.id, progress_callback=progress_callback)
                if run.get("readiness_status") == "ready":
                    return self._finish(
                        run_id,
                        status="completed",
                        stage="Ready for review",
                        message=(
                            "Automatic setup finished. The source passed the safe source test and remains off "
                            "until you explicitly include it in daily runs."
                        ),
                        progress_percent=100,
                        progress_callback=progress_callback,
                    )

                insight = run.get("source_test_insight") if isinstance(run.get("source_test_insight"), dict) else {}
                should_regenerate = self._source_test_proposes_regeneration(insight)
                recipe_attempts = int(run.get("recipe_attempts") or 0)
                max_attempts = int(run.get("max_recipe_attempts") or 3)
                if should_regenerate and recipe_attempts < max_attempts:
                    source = self.source.require_source(source.id)
                    run = self._generate_and_adopt_recipe(
                        run_id,
                        source,
                        source_test_insight=dict(insight.get("generation_clues") or insight),
                        progress_callback=progress_callback,
                    )
                    source = self.source.require_source(source.id)
                    continue

                if should_regenerate:
                    return self._finish(
                        run_id,
                        status="failed",
                        stage="Gave up",
                        message=(
                            f"Source tests still proposed rebuilding the reading plan after {recipe_attempts} "
                            "automatic recipe attempt(s)."
                        ),
                        error_message=str(insight.get("recommendation") or run.get("readiness_summary") or ""),
                        progress_percent=100,
                        progress_callback=progress_callback,
                    )

                return self._finish(
                    run_id,
                    status="blocked",
                    stage="Needs attention",
                    message=str(insight.get("recommendation") or run.get("readiness_summary") or ""),
                    error_message=str(run.get("readiness_summary") or ""),
                    progress_percent=100,
                    progress_callback=progress_callback,
                )
        except Exception as exc:
            return self._finish(
                run_id,
                status="failed",
                stage="Failed",
                message=f"Automatic setup stopped: {exc}",
                error_message=str(exc),
                progress_percent=100,
                progress_callback=progress_callback,
            )

    def context_for_state(self, state) -> dict[str, Any]:
        source = state.source
        show = (
            source.status != "archived"
            and source.kind not in {"manual", "local_yaml"}
            and state.lifecycle.get("state") != "implemented"
        )
        latest = self.latest_for_source(source.id) if show else None
        configured = self.is_configured()
        resume_run_id = (
            str(latest.get("run_id") or "") if latest and str(latest.get("status") or "") in _RESUMABLE_STATUSES else ""
        )
        disabled_reason = ""
        if not configured:
            disabled_reason = "Add an Anthropic API key in Setup before using automatic source setup."
        elif not source.url:
            disabled_reason = "Save a source URL before using automatic source setup."
        can_start = bool(show and configured and source.url)
        latest_status = str(latest.get("status") or "") if latest else ""
        if resume_run_id:
            action_label = "Continue automatic setup" if latest_status in _ACTIVE_STATUSES else "Continue after fixing"
        else:
            action_label = "Automatically set up"
        return {
            "show": show,
            "configured": configured,
            "can_start": can_start,
            "disabled_reason": disabled_reason,
            "latest_run": latest,
            "latest_events": list((latest or {}).get("events") or [])[-5:],
            "resume_run_id": resume_run_id,
            "action_label": action_label,
        }

    def latest_for_source(self, source_id: str) -> dict[str, Any] | None:
        runs = [run for run in self.list_runs() if run.get("source_id") == source_id]
        if not runs:
            return None
        return sorted(runs, key=lambda item: str(item.get("created_at") or ""), reverse=True)[0]

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.runs_dir.exists():
            return []
        runs = []
        for path in sorted(self.runs_dir.glob("*.json")):
            data = read_json(path, {})
            if isinstance(data, dict) and data.get("run_id"):
                runs.append(data)
        return runs

    def active_work_items(self, *, exclude_task_ids: set[str] | None = None) -> list[dict[str, Any]]:
        excluded = exclude_task_ids or set()
        items = []
        for run in self.list_runs():
            if str(run.get("status") or "") not in _ACTIVE_STATUSES:
                continue
            item = self.work_item(run)
            if item["task_id"] not in excluded:
                items.append(item)
        return sorted(items, key=lambda item: str(item.get("started_at") or ""), reverse=True)

    def work_item(self, run: dict[str, Any]) -> dict[str, Any]:
        source_name = str(run.get("source_name") or run.get("source_id") or "source")
        return {
            "kind": "auto_setup",
            "task_id": f"auto-{run.get('run_id')}",
            "run_id": str(run.get("run_id") or ""),
            "source_id": str(run.get("source_id") or ""),
            "source_name": source_name,
            "title": f"Setting up {source_name}",
            "status": str(run.get("status") or "running"),
            "stage": str(run.get("stage") or "Automatic setup"),
            "message": str(run.get("message") or "Preparing source setup."),
            "progress_percent": int(run.get("progress_percent") or 8),
            "started_at": str(run.get("started_at") or run.get("created_at") or ""),
            "finished_at": str(run.get("finished_at") or ""),
            "href": f"/sources/{run.get('source_id')}",
        }

    def load(self, run_id: str) -> dict[str, Any]:
        path = self._run_file(run_id)
        if not path.exists():
            raise ValueError(f"Automatic setup run not found: {run_id}")
        data = read_json(path, {}, strict=True)
        if not isinstance(data, dict):
            raise ValueError(f"Automatic setup run is invalid: {run_id}")
        return data

    def _create_run(self, source_id: str, source_name: str) -> dict[str, Any]:
        now = _now()
        run = {
            "run_id": self._new_run_id(source_id),
            "source_id": source_id,
            "source_name": source_name,
            "status": "pending",
            "stage": "Queued",
            "message": "Automatic setup is queued.",
            "progress_percent": 8,
            "created_at": now,
            "updated_at": now,
            "started_at": "",
            "finished_at": "",
            "error_message": "",
            "recipe_attempts": 0,
            "max_recipe_attempts": 3,
            "recipe_run_ids": [],
            "candidate_ids": [],
            "last_recipe_run_id": "",
            "last_candidate_id": "",
            "last_recipe_path": "",
            "source_test_attempts": 0,
            "last_source_test_status": "",
            "readiness_status": "",
            "readiness_summary": "",
            "source_test_insight": {},
            "events": [
                {
                    "created_at": now,
                    "stage": "Queued",
                    "message": "Automatic setup was queued.",
                }
            ],
        }
        self._write_run(run)
        return run

    def _validate_source(self, source_id: str):
        if not self.is_configured():
            raise ValueError("Automatic setup needs an Anthropic API key before it can learn sources.")
        source = self.source.require_source(source_id)
        self.source.require_not_archived(source)
        if source.kind in {"manual", "local_yaml"}:
            raise ValueError("Automatic setup is only for job-board URL sources.")
        if not source.url:
            raise ValueError("Save a source URL before using automatic setup.")
        return source

    def _generate_and_adopt_recipe(
        self,
        run_id: str,
        source,
        *,
        source_test_insight: dict[str, Any],
        progress_callback=None,
    ) -> dict[str, Any]:
        run = self.load(run_id)
        attempt_number = int(run.get("recipe_attempts") or 0) + 1
        run = self._update_run(
            run_id,
            recipe_attempts=attempt_number,
            stage="Learning source",
            message=f"Generating reading plan attempt {attempt_number} of {run.get('max_recipe_attempts', 3)}.",
            progress_percent=20 if attempt_number == 1 else 45,
        )
        self._append_event(run_id, "Learning source", str(run["message"]))
        self._notify(progress_callback, run)
        recipe_run = self.recipe_runs.start_from_source_capture(
            source.id,
            rendered=None,
            capture_detail=True,
            max_candidates=50,
            refine=True,
            max_attempts=4,
            source_test_insight=source_test_insight,
            run_async=False,
        )
        recipe_run_ids = list(run.get("recipe_run_ids") or [])
        recipe_run_ids.append(str(recipe_run.get("run_id") or ""))
        candidate_id = str(recipe_run.get("candidate_id") or "")
        if str(recipe_run.get("status") or "") != "completed" or not candidate_id:
            raise RuntimeError(str(recipe_run.get("error") or "Recipe generation did not produce a usable plan."))
        candidate = self.candidates.load_candidate(candidate_id)
        if not candidate_is_reviewable(candidate):
            raise RuntimeError("Generated reading plan did not pass local quality checks.")
        recipe_path = str(
            recipe_run.get("approval_recipe_path") or self.approvals.suggested_recipe_path(candidate, source)
        )

        run = self._update_run(
            run_id,
            stage="Saving reading plan",
            message="Approving the generated reading plan and selecting it for this source.",
            progress_percent=32 if attempt_number == 1 else 58,
            recipe_run_ids=recipe_run_ids,
            last_recipe_run_id=str(recipe_run.get("run_id") or ""),
            last_candidate_id=candidate_id,
            candidate_ids=list(run.get("candidate_ids") or []) + [candidate_id],
        )
        self._notify(progress_callback, run)
        approval = self.approvals.approve(
            candidate_id,
            recipe_path,
            source_id=source.id,
            overwrite=True,
            base_url=source.url,
        )
        adoption = self.adoptions.adopt(
            candidate_id,
            source.id,
            prepare_disabled_execution_entry=True,
        )
        preview_count = approval.preview.extracted_job_count if approval.preview else 0
        self._append_event(
            run_id,
            "Reading plan selected",
            f"Selected {adoption.adopted_recipe_path}; preview extracted {preview_count} job(s).",
        )
        return self._update_run(
            run_id,
            last_recipe_path=adoption.adopted_recipe_path,
            stage="Reading plan selected",
            message="Generated reading plan was selected and the daily-run projection is still disabled.",
            progress_percent=38 if attempt_number == 1 else 62,
        )

    def _run_source_test(self, run_id: str, source_id: str, *, progress_callback=None) -> dict[str, Any]:
        run = self.load(run_id)
        test_attempts = int(run.get("source_test_attempts") or 0) + 1
        run = self._update_run(
            run_id,
            source_test_attempts=test_attempts,
            stage="Safe source test",
            message=f"Running safe source test attempt {test_attempts}.",
            progress_percent=70,
        )
        self._append_event(run_id, "Safe source test", str(run["message"]))
        self._notify(progress_callback, run)
        execution = self.source.run_source_test(source_id)
        insight = execution.payload.get("source_test_insight") if isinstance(execution.payload, dict) else {}
        readiness = execution.readiness
        summary = str(getattr(readiness, "readiness_summary", "") or "")
        status = str(getattr(readiness, "readiness_status", "") or "")
        self._append_event(run_id, "Safe source test result", summary or status)
        return self._update_run(
            run_id,
            last_source_test_status=str(getattr(execution.result, "status", "") or ""),
            readiness_status=status,
            readiness_summary=summary,
            source_test_insight=insight if isinstance(insight, dict) else {},
            message=summary or "Safe source test finished.",
            progress_percent=85,
        )

    def _finish(
        self,
        run_id: str,
        *,
        status: str,
        stage: str,
        message: str,
        progress_percent: int,
        error_message: str = "",
        progress_callback=None,
    ) -> dict[str, Any]:
        run = self._update_run(
            run_id,
            status=status,
            stage=stage,
            message=message,
            error_message=error_message,
            progress_percent=progress_percent,
            finished_at=_now(),
        )
        self._append_event(run_id, stage, message)
        run = self.load(run_id)
        self._notify(progress_callback, run)
        return run

    def _source_test_proposes_regeneration(self, insight: dict[str, Any]) -> bool:
        action = insight.get("action") if isinstance(insight, dict) else {}
        action_path = str(action.get("action") or "") if isinstance(action, dict) else ""
        if action_path.endswith("/reading-plan/rebuild-from-test"):
            return True
        haystack = " ".join(
            [
                str(insight.get("title") or ""),
                str(insight.get("recommendation") or ""),
            ]
        ).lower()
        return "rebuild" in haystack and "reading plan" in haystack

    def _append_event(self, run_id: str, stage: str, message: str) -> None:
        run = self.load(run_id)
        events = list(run.get("events") or [])
        events.append({"created_at": _now(), "stage": stage, "message": message})
        self._update_run(run_id, events=events[-40:])

    def _update_run(self, run_id: str, **updates: Any) -> dict[str, Any]:
        run = self.load(run_id)
        run.update(updates)
        run["updated_at"] = _now()
        self._write_run(run)
        return run

    def _write_run(self, run: dict[str, Any]) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        write_json(self._run_file(str(run["run_id"])), run)

    def _run_file(self, run_id: str) -> Path:
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError(f"Invalid automatic setup run id: {run_id}")
        return self.runs_dir / f"{run_id}.json"

    def _new_run_id(self, source_id: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", source_id.lower()).strip("-") or "source"
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        unique = uuid4().hex[:6]
        return f"{stamp}-{slug}-{unique}"[:100].strip("-")

    def _notify(self, progress_callback, run: dict[str, Any]) -> None:
        if progress_callback:
            progress_callback(self.work_item(run))


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
