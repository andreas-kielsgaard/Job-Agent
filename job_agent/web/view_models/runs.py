from __future__ import annotations

from pathlib import Path

from job_agent.config import ROOT
from job_agent.run_store import RunStore
from job_agent.services.package_index_service import PackageIndexService
from job_agent.token_usage import TokenUsageStore


def build_run_list_view(view: str, root: Path = ROOT) -> dict:
    store = RunStore(root)
    if view == "test":
        runs = [
            run
            for run in store.list_runs(include_archived=True, include_deleted=False, include_tests=True)
            if run.is_test and run.visibility == "active"
        ]
    elif view == "archived":
        runs = [
            run
            for run in store.list_runs(include_archived=True, include_deleted=False, include_tests=True)
            if run.visibility == "archived"
        ]
    elif view == "deleted":
        runs = [
            run
            for run in store.list_runs(include_archived=True, include_deleted=True, include_tests=True)
            if run.visibility == "deleted"
        ]
    else:
        runs = store.list_runs(include_tests=False)
    return {"runs": runs, "view": view}


def build_run_detail_view(
    run_id: str, category: str = "", app_status: str = "", source: str = "", root: Path = ROOT
) -> dict:
    store = RunStore(root)
    record = store.get(run_id)
    if not record:
        raise KeyError(run_id)
    packages = PackageIndexService(root).list_packages(run_id)
    if category:
        packages = [pkg for pkg in packages if pkg.get("match_category") == category]
    if app_status:
        packages = [pkg for pkg in packages if pkg.get("application_status") == app_status]
    if source:
        packages = [pkg for pkg in packages if source.lower() in str(pkg.get("source_url", "")).lower()]
    source_warnings = [event for event in store.read_events(run_id) if event.get("event_type") == "source_warning"]
    return {
        "run": record,
        "packages": packages,
        "events": store.read_events(run_id, limit=12),
        "source_warnings": source_warnings,
        "token_records": TokenUsageStore(root).list_for_run(run_id),
        "filters": {"category": category, "app_status": app_status, "source": source},
    }
