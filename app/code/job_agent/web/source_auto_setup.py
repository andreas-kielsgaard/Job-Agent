from __future__ import annotations

import re
from datetime import UTC, datetime
from functools import lru_cache
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
from job_agent.services.source_disqualification_service import SourceDisqualificationService
from job_agent.services.source_url_assessment import assess_source_setup_url
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
        self.disqualifications = SourceDisqualificationService(self.root)
        self.runs_dir = output_dir(self.root) / "source-auto-setup-runs"

    def is_configured(self) -> bool:
        try:
            return LlmService(self.root).is_configured()
        except (RuntimeError, ValueError):
            return False

    def prepare(self, source_id: str, *, run_id: str = "", llm_model: str = "") -> dict[str, Any]:
        if run_id.strip():
            run = self.load(run_id)
            if run.get("source_id") != source_id:
                raise ValueError("Automatic setup run does not belong to this source.")
            if run.get("status") == "completed":
                return run
            self._validate_source(source_id)
            if llm_model.strip():
                run = self._update_run(str(run["run_id"]), llm_model=llm_model)
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
        return self._create_run(source.id, source.name, llm_model=llm_model)

    def run(self, run_id: str, *, progress_callback=None) -> dict[str, Any]:
        run = self.load(run_id)
        if run.get("status") == "completed":
            return run

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

            if not source.recipe_path and not self.is_configured():
                return self._finish(
                    run_id,
                    status="failed",
                    stage="API key required",
                    message="Automatic setup needs an Anthropic API key before it can learn a new reading plan.",
                    error_message="ANTHROPIC_API_KEY is missing or placeholder.",
                    progress_percent=100,
                    progress_callback=progress_callback,
                )

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
                message="Creating or refreshing the daily-run projection while preserving its enabled/off state.",
                progress_percent=max(35, int(run.get("progress_percent") or 35)),
            )
            self._notify(progress_callback, run)
            self.source.create_or_update_execution_source(source.id, preserve_enabled=True)
            tested_recipe_signatures = set(str(item) for item in run.get("tested_recipe_signatures") or [])

            while True:
                run = self.load(run_id)
                source = self.source.require_source(source.id)
                recipe_signature = self._recipe_test_signature(run, source)
                previous_decision = (
                    run.get("source_test_decision") if isinstance(run.get("source_test_decision"), dict) else {}
                )
                source_test_attempts = int(run.get("source_test_attempts") or 0)
                max_source_test_attempts = int(run.get("max_source_test_attempts") or 2)
                retry_same_recipe = bool(previous_decision.get("should_retry_source_test")) and (
                    source_test_attempts < max_source_test_attempts
                )
                if recipe_signature and recipe_signature in tested_recipe_signatures and not retry_same_recipe:
                    return self._finish(
                        run_id,
                        status="blocked",
                        stage="Needs new reading plan",
                        message=(
                            "The safe source test already failed for this unchanged reading plan. "
                            "Regenerate or edit the plan before testing it again."
                        ),
                        error_message=str(run.get("readiness_summary") or ""),
                        progress_percent=100,
                        progress_callback=progress_callback,
                    )
                run = self._run_source_test(run_id, source.id, progress_callback=progress_callback)
                if recipe_signature:
                    tested_recipe_signatures.add(recipe_signature)
                    run = self._update_run(run_id, tested_recipe_signatures=sorted(tested_recipe_signatures))
                if run.get("readiness_status") == "ready":
                    completion_message = (
                        "Automatic setup finished. The source passed the safe source test and remains included "
                        "in daily runs."
                        if self._source_execution_enabled(source.id)
                        else (
                            "Automatic setup finished. The source passed the safe source test and remains off "
                            "until you explicitly include it in daily runs."
                        )
                    )
                    return self._finish(
                        run_id,
                        status="completed",
                        stage="Ready for review",
                        message=completion_message,
                        progress_percent=100,
                        progress_callback=progress_callback,
                    )

                insight = run.get("source_test_insight") if isinstance(run.get("source_test_insight"), dict) else {}
                decision = run.get("source_test_decision") if isinstance(run.get("source_test_decision"), dict) else {}
                source_test_attempts = int(run.get("source_test_attempts") or 0)
                max_source_test_attempts = int(run.get("max_source_test_attempts") or 2)
                if bool(decision.get("should_retry_source_test")) and source_test_attempts < max_source_test_attempts:
                    run = self._update_run(
                        run_id,
                        stage="Retrying safe source test",
                        message=(
                            "The source test passed capability checks but hit transient fetch warnings. "
                            "Retrying once before asking for review."
                        ),
                        progress_percent=72,
                    )
                    self._append_event(run_id, "Retrying safe source test", str(run["message"]))
                    self._notify(progress_callback, run)
                    continue
                should_regenerate = bool(decision.get("should_regenerate_recipe")) or self._source_test_proposes_regeneration(insight)
                recipe_attempts = int(run.get("recipe_attempts") or 0)
                max_attempts = int(run.get("max_recipe_attempts") or 3)
                if should_regenerate and not self.is_configured():
                    return self._finish(
                        run_id,
                        status="blocked",
                        stage="API key required",
                        message=(
                            "The safe source test ran, but fixing this source now requires learning a revised "
                            "reading plan. Add an Anthropic API key, then continue automatic setup."
                        ),
                        error_message=str(insight.get("recommendation") or run.get("readiness_summary") or ""),
                        progress_percent=100,
                        progress_callback=progress_callback,
                    )
                if should_regenerate and recipe_attempts < max_attempts:
                    previous_signature = recipe_signature
                    source = self.source.require_source(source.id)
                    run = self._generate_and_adopt_recipe(
                        run_id,
                        source,
                        source_test_insight=dict(insight.get("generation_clues") or insight),
                        progress_callback=progress_callback,
                    )
                    source = self.source.require_source(source.id)
                    if previous_signature and self._recipe_test_signature(run, source) == previous_signature:
                        return self._finish(
                            run_id,
                            status="blocked",
                            stage="Needs new reading plan",
                            message=(
                                "The source test asked for a rebuild, but automatic learning did not produce a "
                                "new reading plan to test."
                            ),
                            error_message=str(run.get("readiness_summary") or ""),
                            progress_percent=100,
                            progress_callback=progress_callback,
                        )
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
                    message=str(decision.get("summary") or insight.get("recommendation") or run.get("readiness_summary") or ""),
                    error_message=str(run.get("readiness_summary") or ""),
                    progress_percent=100,
                    progress_callback=progress_callback,
                )
        except Exception as exc:
            if _is_playwright_dependency_error(exc):
                return self._finish(
                    run_id,
                    status="blocked",
                    stage="Browser support required",
                    message=_playwright_dependency_message(),
                    error_message=str(exc),
                    progress_percent=100,
                    progress_callback=progress_callback,
                )
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
        configured = self.is_configured()
        auto_state = self.source.auto_setup_state(
            source,
            state.lifecycle,
            readiness=state.readiness,
            index_status=state.index,
            auto_setup_configured=configured,
        )
        show = bool(auto_state.get("show"))
        latest = self.latest_for_source(source.id) if show else None
        latest_status = str(latest.get("status") or "") if latest else ""
        setup_complete = bool(auto_state.get("setup_complete"))
        resume_run_id = (
            str(latest.get("run_id") or "")
            if latest and latest_status in _RESUMABLE_STATUSES and (latest_status in _ACTIVE_STATUSES or not setup_complete)
            else ""
        )
        if resume_run_id:
            action_label = "Continue automatic setup" if latest_status in _ACTIVE_STATUSES else "Continue after fixing"
        else:
            action_label = str(auto_state.get("label") or "Automatically set up")
        return {
            "show": show,
            "configured": configured,
            "can_start": bool(auto_state.get("can_start")),
            "disabled_reason": str(auto_state.get("disabled_reason") or ""),
            "latest_run": latest,
            "latest_events": list((latest or {}).get("events") or [])[-5:],
            "resume_run_id": resume_run_id,
            "action_label": action_label,
            "requires_llm": bool(auto_state.get("requires_llm")),
            "stale_recipe_source_test": bool(auto_state.get("stale_recipe_source_test")),
            "setup_complete": setup_complete,
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

    def monitor_context(self, *, source_id: str = "", message: str = "", warning: str = "") -> dict[str, Any]:
        runs = self.list_runs()
        if source_id:
            runs = [run for run in runs if str(run.get("source_id") or "") == source_id]
        runs = _latest_run_per_source(runs)[:50]
        source_ids = {str(run.get("source_id") or "") for run in runs if str(run.get("source_id") or "")}
        sources = [source for source in self.source.registry.list_saved_sources(include_stats=False) if source.id in source_ids]
        source_by_id = {source.id: source for source in sources}
        execution_by_id = self.source.saved_execution_by_source(sources)
        readiness_by_id = self.source.current_readiness_by_source(sources)
        index_by_id = self.source.index_store.summaries_by_source()
        items = [
            self.monitor_item(
                run,
                source=source_by_id.get(str(run.get("source_id") or "")),
                execution_entry=execution_by_id.get(str(run.get("source_id") or "")),
                readiness=readiness_by_id.get(str(run.get("source_id") or "")),
                index=index_by_id.get(str(run.get("source_id") or "")),
            )
            for run in runs
        ]
        running_statuses = {"pending", "running"}
        completed = sum(1 for item in items if item["status"] == "completed")
        blocked = sum(1 for item in items if item["status"] in {"blocked", "failed"})
        running = sum(1 for item in items if item["status"] in running_statuses)
        return {
            "title": "Automatic Source Preparation",
            "source_filter": source_id,
            "message": message,
            "warning": warning,
            "auto_setup_runs": items,
            "auto_setup_summary": {
                "total": len(items),
                "running": running,
                "completed": completed,
                "blocked": blocked,
                "progress_percent": _summary_progress(items),
                "is_active": running > 0,
            },
        }

    def monitor_payload(self, *, source_id: str = "") -> dict[str, Any]:
        context = self.monitor_context(source_id=source_id)
        return {
            "source_filter": context["source_filter"],
            "runs": context["auto_setup_runs"],
            "summary": context["auto_setup_summary"],
        }

    def monitor_item(
        self,
        run: dict[str, Any],
        *,
        source=None,
        execution_entry=None,
        readiness=None,
        index=None,
    ) -> dict[str, Any]:
        source_id = str(run.get("source_id") or "")
        if source is None and source_id:
            source = self.source.registry.get_source(source_id)
        if execution_entry is None and source_id:
            execution_entry = self.source.execution.find_by_source_id(source_id)
        if readiness is None and source_id:
            readiness = self.source.readiness.load(source_id)
        if source and readiness:
            readiness = self.source.readiness.with_current_recipe_file_checks(source, readiness)
        if index is None:
            index = self.source.index_store.summary_for_source(source_id, str(run.get("source_name") or source_id))
        readiness_status = str(getattr(readiness, "readiness_status", "") or run.get("readiness_status") or "")
        execution_enabled = bool(execution_entry and execution_entry.get("enabled", True))
        status = str(run.get("status") or "pending")
        stage = str(run.get("stage") or "Queued")
        dependency_blocked = _is_playwright_dependency_error(
            " ".join(
                [
                    str(run.get("message") or ""),
                    str(run.get("error_message") or ""),
                    str(run.get("readiness_summary") or ""),
                ]
            )
        )
        dependency_available = dependency_blocked and _rendered_browser_available()
        if dependency_blocked and not dependency_available:
            status = "blocked"
            stage = "Browser support required"
        elif dependency_blocked:
            status = "blocked"
            stage = "Retry ready"
        if readiness_status == "ready" and status not in _ACTIVE_STATUSES:
            status = "completed"
            if stage in {"Failed", "Needs attention", "Browser support required", "Retry ready"}:
                stage = "Ready for review"
        source_test_attempts = int(run.get("source_test_attempts") or 0)
        recipe_attempts = int(run.get("recipe_attempts") or 0)
        if readiness_status == "ready" and execution_enabled:
            applied_label = "Applied"
            applied_summary = "Source test passed and the daily-run entry is enabled."
            applied_badge = "high"
        elif readiness_status == "ready":
            applied_label = "Passed, off"
            applied_summary = "Source test passed; daily-run entry is still off."
            applied_badge = "medium"
        elif dependency_blocked and not dependency_available:
            applied_label = "Browser support required"
            applied_summary = _playwright_dependency_message()
            applied_badge = "warning"
        elif dependency_blocked:
            applied_label = "Retry ready"
            applied_summary = (
                "Rendered browser support is available now. Continue automatic setup to retry this source "
                "with the repaired environment."
            )
            applied_badge = "medium"
        elif status in {"blocked", "failed"}:
            applied_label = "Not applied"
            applied_summary = str(run.get("message") or getattr(readiness, "readiness_summary", "") or "")
            applied_badge = "low"
        else:
            applied_label = "In progress"
            applied_summary = str(run.get("message") or "Automatic preparation is running.")
            applied_badge = "medium"
        return {
            "run_id": str(run.get("run_id") or ""),
            "source_id": source_id,
            "source_name": str(run.get("source_name") or source_id),
            "status": status,
            "stage": stage,
            "message": str(run.get("message") or ""),
            "progress_percent": int(run.get("progress_percent") or 8),
            "created_at": str(run.get("created_at") or ""),
            "updated_at": str(run.get("updated_at") or ""),
            "finished_at": str(run.get("finished_at") or ""),
            "error_message": str(run.get("error_message") or ""),
            "recipe_attempts": recipe_attempts,
            "max_recipe_attempts": int(run.get("max_recipe_attempts") or 3),
            "source_test_attempts": source_test_attempts,
            "readiness_status": readiness_status or "untested",
            "readiness_summary": str(
                getattr(readiness, "readiness_summary", "") or run.get("readiness_summary") or ""
            ),
            "execution_enabled": execution_enabled,
            "indexed_count": int(getattr(index, "indexed_count", 0) or 0),
            "applied_label": applied_label,
            "applied_summary": applied_summary,
            "applied_badge": applied_badge,
            "events": list(run.get("events") or [])[-6:],
            "href": f"/sources/{source_id}" if source_id else "/sources",
        }

    def work_item(self, run: dict[str, Any]) -> dict[str, Any]:
        source_name = str(run.get("source_name") or run.get("source_id") or "source")
        source_id = str(run.get("source_id") or "")
        return {
            "kind": "auto_setup",
            "task_id": f"auto-{run.get('run_id')}",
            "run_id": str(run.get("run_id") or ""),
            "source_id": source_id,
            "source_name": source_name,
            "title": f"Setting up {source_name}",
            "status": str(run.get("status") or "running"),
            "stage": str(run.get("stage") or "Automatic setup"),
            "message": str(run.get("message") or "Preparing source setup."),
            "progress_percent": int(run.get("progress_percent") or 8),
            "started_at": str(run.get("started_at") or run.get("created_at") or ""),
            "finished_at": str(run.get("finished_at") or ""),
            "href": f"/sources/auto-setup?source_id={source_id}" if source_id else "/sources/auto-setup",
        }

    def load(self, run_id: str) -> dict[str, Any]:
        path = self._run_file(run_id)
        if not path.exists():
            raise ValueError(f"Automatic setup run not found: {run_id}")
        data = read_json(path, {}, strict=True)
        if not isinstance(data, dict):
            raise ValueError(f"Automatic setup run is invalid: {run_id}")
        return data

    def _create_run(self, source_id: str, source_name: str, *, llm_model: str = "") -> dict[str, Any]:
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
            "max_source_test_attempts": 2,
            "recipe_run_ids": [],
            "candidate_ids": [],
            "last_recipe_run_id": "",
            "last_candidate_id": "",
            "last_recipe_path": "",
            "source_test_attempts": 0,
            "tested_recipe_signatures": [],
            "last_source_test_status": "",
            "readiness_status": "",
            "readiness_summary": "",
            "source_test_insight": {},
            "source_test_decision": {},
            "llm_model": llm_model,
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

    def _validate_source(self, source_id: str, *, require_llm: bool | None = None):
        source = self.source.require_source(source_id)
        self.source.require_not_archived(source)
        if source.kind in {"manual", "local_yaml"}:
            raise ValueError("Automatic setup is only for job-board URL sources.")
        has_recipe = bool(str(getattr(source, "recipe_path", "") or "").strip())
        if require_llm is None:
            require_llm = not has_recipe
        if require_llm and not self.is_configured():
            raise ValueError("Automatic setup needs an Anthropic API key before it can learn sources.")
        if not source.url:
            raise ValueError("Save a source URL before using automatic setup.")
        disqualification = self.disqualifications.disqualification_for_url(source.url)
        if disqualification:
            raise ValueError(f"This domain is disqualified from automatic setup: {disqualification.reason}")
        assessment = assess_source_setup_url(source.url) if not has_recipe else None
        if assessment and not assessment.can_auto_setup:
            raise ValueError(assessment.message)
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
            llm_model=str(run.get("llm_model") or ""),
        )
        recipe_run_ids = list(run.get("recipe_run_ids") or [])
        recipe_run_ids.append(str(recipe_run.get("run_id") or ""))
        candidate_id = str(recipe_run.get("candidate_id") or "")
        if str(recipe_run.get("status") or "") != "completed" or not candidate_id:
            error = str(recipe_run.get("error") or "Recipe generation did not produce a usable plan.")
            if _is_playwright_dependency_error(error):
                raise RuntimeError(_playwright_dependency_message())
            raise RuntimeError(error)
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

        def test_progress(event: dict[str, Any]) -> None:
            phase = str(event.get("phase") or event.get("stage") or "Safe source test")
            detail = str(event.get("detail") or event.get("message") or "").strip()
            current = self.load(run_id)
            self._notify(
                progress_callback,
                {
                    **current,
                    "status": "running",
                    "stage": phase,
                    "message": detail or phase,
                    "progress_percent": min(84, max(72, int(current.get("progress_percent") or 72))),
                },
            )

        execution = self.source.run_source_test(source_id, progress_callback=test_progress)
        insight = execution.payload.get("source_test_insight") if isinstance(execution.payload, dict) else {}
        decision = execution.payload.get("source_test_decision") if isinstance(execution.payload, dict) else {}
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
            source_test_decision=decision if isinstance(decision, dict) else {},
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
        action_type = str(action.get("type") or "") if isinstance(action, dict) else ""
        action_path = str(action.get("action") or "") if isinstance(action, dict) else ""
        if action_path.endswith("/reading-plan/rebuild-from-test"):
            return True
        if action_type and action_type != "post":
            return False
        haystack = " ".join(
            [
                str(insight.get("title") or ""),
                str(insight.get("recommendation") or ""),
            ]
        ).lower()
        return "rebuild" in haystack and "reading plan" in haystack

    def _source_execution_enabled(self, source_id: str) -> bool:
        entry = self.source.execution.find_by_source_id(source_id)
        return bool(entry and entry.get("enabled", True))

    def _recipe_test_signature(self, run: dict[str, Any], source) -> str:
        recipe_path = str(getattr(source, "recipe_path", "") or run.get("last_recipe_path") or "")
        if not recipe_path:
            return ""
        return "|".join(
            [
                recipe_path,
                str(run.get("last_candidate_id") or ""),
                str(run.get("last_recipe_run_id") or ""),
            ]
        )

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


def _summary_progress(items: list[dict[str, Any]]) -> int:
    if not items:
        return 0
    return round(sum(int(item.get("progress_percent") or 0) for item in items) / len(items))


def _latest_run_per_source(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in sorted(runs, key=_run_sort_key, reverse=True):
        key = str(run.get("source_id") or run.get("run_id") or "")
        if not key:
            continue
        grouped.setdefault(key, []).append(run)

    selected: list[dict[str, Any]] = []
    for group in grouped.values():
        active = [run for run in group if str(run.get("status") or "") in _ACTIVE_STATUSES]
        selected.append(active[0] if active else group[0])
    return sorted(selected, key=_run_sort_key, reverse=True)


def _run_sort_key(run: dict[str, Any]) -> tuple[str, str]:
    return (str(run.get("created_at") or ""), str(run.get("run_id") or ""))


def _is_playwright_dependency_error(error: object) -> bool:
    text = str(error or "").lower()
    return "playwright" in text and any(
        marker in text
        for marker in [
            "unavailable",
            "not installed",
            "no module named",
            "requirements-playwright",
            "rendered mode requested",
            "use rendered_html recipes",
            "chromium",
        ]
    )


@lru_cache(maxsize=1)
def _rendered_browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def _playwright_dependency_message() -> str:
    return (
        "Rendered browser mode is required for this source, but Playwright/Chromium is not available. "
        "Install app/environment/requirements-playwright.txt and Chromium, then continue automatic preparation."
    )
