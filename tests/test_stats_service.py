from __future__ import annotations

from pathlib import Path

from tests.helpers import write_sample_package

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.run_store import RunOptions, RunStore
from job_agent.services.stats_service import StatsService


def test_dashboard_stats_handles_no_runs(project_root: Path) -> None:
    stats = StatsService(project_root).build_dashboard_stats([])

    assert stats["latest_is_today"] is False
    assert stats["jobs_found_today"] == 0
    assert stats["applied_last_7"] == 0


def test_dashboard_stats_counts_today_active_and_recent_applied(project_root: Path) -> None:
    store = RunStore(project_root)
    active = store.create_run(RunOptions())
    store.update(active.run_id, status="running", total_loaded=2, new_roles=1)
    old = store.create_run(RunOptions())
    store.update(old.run_id, started_at="2026-01-01T00:00:00+00:00", status="completed", total_loaded=5)

    status_store = ApplicationStatusStore(project_root)
    status_store.ensure_for_job(
        stable_id="stable-1",
        fuzzy_key="fuzzy-1",
        title="SAP ABAP",
        company="Recruiter",
        source="Sample",
        url="https://example.com",
        application_url="https://example.com/apply",
    )
    status_store.update_status("stable-1", "applied")

    stats = StatsService(project_root).build_dashboard_stats(store.list_runs())

    assert stats["active_run"].run_id == active.run_id
    assert stats["jobs_found_today"] == 2
    assert stats["applied_last_7"] == 1


def test_stats_page_handles_no_packages_and_average_score(project_root: Path) -> None:
    empty = StatsService(project_root).build_stats_page()
    assert empty["stats"]["avg_score"] == 0

    write_sample_package(project_root)
    with_package = StatsService(project_root).build_stats_page()
    assert with_package["stats"]["unique_jobs"] == 1
    assert with_package["stats"]["avg_score"] == 82
