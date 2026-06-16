from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.run_store import RunRecord, RunStore

from .package_index_service import PackageIndexService


class StatsService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.package_service = PackageIndexService(root)

    def build_dashboard_stats(self, runs: list[RunRecord]) -> dict[str, Any]:
        today = date.today().isoformat()
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
        all_packages = self.package_service.list_packages()
        today_packages = [
            package
            for package in all_packages
            if self.package_service.infer_package_date(package).isoformat() == today
        ]
        today_review_picks = [
            package
            for package in today_packages
            if package.get("review_list", True) and package.get("match_category") in {"strong", "exploratory"}
        ]
        unique_jobs = self.package_service.list_unique_jobs()
        listings_read_today = sum(run.total_loaded for run in today_runs)
        new_roles_today = sum(run.new_roles for run in today_runs)
        changed_roles_today = sum(run.changed_roles for run in today_runs)
        new_changed_roles_today = new_roles_today + changed_roles_today
        source_issues_today = sum(run.source_warnings for run in today_runs)
        return {
            "today": today,
            "latest_is_today": latest_is_today,
            "active_run": active_run,
            "jobs_found_today": listings_read_today,
            "listings_read_today": listings_read_today,
            "new_roles_today": new_roles_today,
            "changed_roles_today": changed_roles_today,
            "new_changed_roles_today": new_changed_roles_today,
            "known_or_repeated_listings_today": max(0, listings_read_today - new_changed_roles_today),
            "review_picks_today": len(today_review_picks),
            "unreviewed_review_picks_today": sum(
                1 for job in today_review_picks if job.get("application_status") == "unreviewed"
            ),
            "source_issues_today": source_issues_today,
            "unseen_jobs": sum(
                1
                for job in unique_jobs
                if job.get("state") in {"new", "changed"} and job.get("application_status") == "unreviewed"
            ),
            "applied_last_7": applied_last_7,
            "today_jobs_url": _jobs_url(
                {
                    "date_from": today,
                    "date_to": today,
                    "dedupe": "0",
                    "category_include": ["strong", "exploratory", "weak", "excluded", "not_scored"],
                    "posting_status_include": ["active", "no_longer_posted", "unknown"],
                }
            ),
            "today_review_url": _jobs_url(
                {
                    "date_from": today,
                    "date_to": today,
                    "dedupe": "0",
                    "category_include": ["strong", "exploratory"],
                    "app_status_include": ["unreviewed"],
                    "posting_status_include": ["active", "unknown"],
                }
            ),
            "unreviewed_jobs_url": _jobs_url(
                {
                    "app_status_include": ["unreviewed"],
                    "category_include": ["strong", "exploratory"],
                    "posting_status_include": ["active", "unknown"],
                }
            ),
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


def _jobs_url(params: dict[str, Any]) -> str:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, list):
            pairs.extend((key, str(item)) for item in value)
        else:
            pairs.append((key, str(value)))
    return f"/jobs?{urlencode(pairs)}"
