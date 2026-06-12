from __future__ import annotations

from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.run_store import RunRecord, RunStore
from job_agent.web.view_models.runs import build_source_progress


class WorkStatusWidgetHandler:
    """Build global work-status widgets from raw runtime work.

    Runtime owns background execution. This handler owns presentation policy: daily
    runs collapse to one aggregate widget, while source indexing, sessions, and
    profile work keep their own task-shaped widgets.
    """

    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)

    def active_work_payload(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        profile_tasks = list(snapshot.get("profile_tasks") or [])
        return {
            "sources": [
                *self._daily_run_widgets(snapshot.get("active_run")),
                *self._source_index_widgets(snapshot.get("index_tasks") or []),
                *self._source_session_widgets(snapshot.get("session_tasks") or []),
                *self._source_auto_setup_widgets(snapshot.get("auto_setup_tasks") or []),
                *self._source_auto_setup_widgets(snapshot.get("persisted_auto_setup_tasks") or []),
                *self._profile_widgets(profile_tasks),
                *self._profile_widgets(snapshot.get("persisted_profile_tasks") or []),
            ]
        }

    def _daily_run_widgets(self, active_run: RunRecord | None) -> list[dict[str, Any]]:
        if not active_run:
            return []
        events = RunStore(self.root).read_events(active_run.run_id)
        progress = build_source_progress(events)
        summary = progress["summary"]
        total_sources = int(summary.get("total_sources") or 0)
        finished_sources = int(summary.get("sources_completed") or 0) + int(summary.get("sources_failed") or 0)
        running_sources = int(summary.get("sources_running") or 0)
        waiting_sources = int(summary.get("sources_waiting") or 0)
        latest = next((event for event in reversed(events) if str(event.get("message") or "").strip()), {})
        if total_sources:
            stage = f"{finished_sources}/{total_sources} sources finished"
            message = (
                f"{running_sources} running in parallel, {waiting_sources} waiting. "
                f"{int(summary.get('jobs_found_so_far') or 0)} jobs found so far."
            )
        else:
            stage = "Starting daily run"
            message = str(latest.get("message") or "Preparing source checks.")
        return [
            {
                "kind": "run",
                "task_id": f"run-{active_run.run_id}",
                "run_id": active_run.run_id,
                "source_id": "",
                "source_name": "Daily run",
                "title": "Test daily run" if active_run.is_test else "Daily run",
                "status": active_run.status,
                "stage": stage,
                "message": message,
                "progress_percent": int(
                    summary.get("progress_percent") or (8 if active_run.status in {"pending", "running"} else 100)
                ),
                "href": f"/runs/{active_run.run_id}",
            }
        ]

    def _source_index_widgets(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(tasks)

    def _source_session_widgets(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(tasks)

    def _source_auto_setup_widgets(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(tasks)

    def _profile_widgets(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(tasks)
