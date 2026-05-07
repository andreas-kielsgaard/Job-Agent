from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.run_store import RunRecord, RunStore

from .package_index_service import PackageIndexService


class StatsService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.package_service = PackageIndexService(root)

    def build_dashboard_stats(self, runs: list[RunRecord]) -> dict[str, Any]:
        today = datetime.now().date().isoformat()
        latest_run = runs[0] if runs else None
        latest_is_today = bool(latest_run and latest_run.started_at.startswith(today))
        active_run = next((run for run in runs if run.status in {"pending", "running"}), None)
        today_runs = [run for run in runs if run.started_at.startswith(today)]
        status_store = ApplicationStatusStore(self.root)
        try:
            statuses = status_store.list_all()
        except ValueError:
            status_store.recover_corrupt_status_file()
            statuses = []
        seven_days_ago = datetime.now(UTC).timestamp() - 7 * 24 * 60 * 60
        applied_last_7 = 0
        for record in statuses:
            if record.status != "applied" or not record.applied_at:
                continue
            try:
                applied_at = datetime.fromisoformat(record.applied_at).timestamp()
            except ValueError:
                continue
            if applied_at >= seven_days_ago:
                applied_last_7 += 1
        unique_jobs = self.package_service.list_unique_jobs()
        return {
            "latest_is_today": latest_is_today,
            "active_run": active_run,
            "jobs_found_today": sum(run.total_loaded for run in today_runs),
            "new_roles_today": sum(run.new_roles for run in today_runs),
            "unseen_jobs": sum(
                1
                for job in unique_jobs
                if job.get("state") in {"new", "changed"} and job.get("application_status") == "unreviewed"
            ),
            "applied_last_7": applied_last_7,
        }

    def build_stats_page(self) -> dict[str, Any]:
        run_store = RunStore(self.root)
        try:
            runs = run_store.list_runs(include_tests=False)
        except ValueError:
            run_store.recover_corrupt_registry()
            runs = []
        packages = self.package_service.list_unique_jobs()
        status_store = ApplicationStatusStore(self.root)
        try:
            statuses = status_store.list_all()
        except ValueError:
            status_store.recover_corrupt_status_file()
            statuses = []
        status_counts: dict[str, int] = {}
        for record in statuses:
            status_counts[record.status] = status_counts.get(record.status, 0) + 1
        stats = {
            "total_runs": len(runs),
            "completed_runs": sum(1 for run in runs if run.status == "completed"),
            "total_loaded": sum(run.total_loaded for run in runs),
            "total_generated": sum(run.generated_job_count for run in runs),
            "unique_jobs": len(packages),
            "strong_jobs": sum(1 for job in packages if job.get("match_category") == "strong"),
            "exploratory_jobs": sum(1 for job in packages if job.get("match_category") == "exploratory"),
            "applied_total": status_counts.get("applied", 0),
            "interesting_total": status_counts.get("interesting", 0),
            "not_interesting_total": status_counts.get("not_interesting", 0),
            "avg_score": round(sum(job.get("match_score", 0) for job in packages) / len(packages), 1)
            if packages
            else 0,
        }
        return {"stats": stats, "status_counts": status_counts, "runs": runs[:10]}
