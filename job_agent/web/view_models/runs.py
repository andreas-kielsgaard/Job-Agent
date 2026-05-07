from __future__ import annotations

from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.run_store import RunStore
from job_agent.services.package_index_service import PackageIndexService
from job_agent.token_usage import TokenUsageStore


def build_run_list_view(view: str, root: Path = ROOT) -> dict:
    store = RunStore(root)
    try:
        all_runs = store.list_runs(include_archived=True, include_deleted=True, include_tests=True)
    except ValueError:
        store.recover_corrupt_registry()
        all_runs = []
    if view == "test":
        runs = [run for run in all_runs if run.is_test and run.visibility == "active"]
    elif view == "archived":
        runs = [run for run in all_runs if run.visibility == "archived"]
    elif view == "deleted":
        runs = [run for run in all_runs if run.visibility == "deleted"]
    else:
        runs = [run for run in all_runs if run.visibility == "active" and not run.is_test]
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
    all_events = store.read_events(run_id)
    source_warnings = [event for event in all_events if event.get("event_type") == "source_warning"]
    source_progress = build_source_progress(all_events)
    return {
        "run": record,
        "packages": packages,
        "events": all_events[-12:],
        "source_warnings": source_warnings,
        "source_progress": source_progress["items"],
        "source_progress_summary": source_progress["summary"],
        "token_records": TokenUsageStore(root).list_for_run(run_id),
        "filters": {"category": category, "app_status": app_status, "source": source},
    }


def build_source_progress(events: list[dict[str, Any]]) -> dict[str, Any]:
    items_by_index: dict[int, dict[str, Any]] = {}
    source_count = 0

    for event in events:
        if event.get("phase") != "source_ingestion" or not str(event.get("event_type", "")).startswith("source_"):
            continue
        counts = event.get("counts") or {}
        source_index = int(counts.get("source_index") or 0)
        if source_index <= 0:
            continue
        source_count = max(source_count, int(counts.get("source_count") or 0), source_index)
        item = items_by_index.setdefault(source_index, _waiting_source_item(source_index, source_count))
        source_name = event.get("current_source") or item["source_name"]
        item.update(
            {
                "source_name": source_name,
                "source_index": source_index,
                "source_count": int(counts.get("source_count") or item.get("source_count") or source_count),
                "latest_message": event.get("message", ""),
            }
        )
        elapsed = counts.get("elapsed_time_seconds")
        if elapsed is not None:
            item["elapsed_time_seconds"] = elapsed

        event_type = event.get("event_type")
        if event_type == "source_started":
            item["status"] = "running"
            item["started_at"] = event.get("timestamp", "")
        elif event_type == "source_warning":
            item["warnings_count"] = max(item["warnings_count"], 0) + int(counts.get("warnings_count") or 1)
        elif event_type == "source_completed":
            item["jobs_found"] = int(counts.get("jobs_found") or 0)
            item["warnings_count"] = max(item["warnings_count"], int(counts.get("warnings_count") or 0))
            item["status"] = "warning" if item["warnings_count"] > 0 else "completed"
            item["finished_at"] = event.get("timestamp", "")
        elif event_type == "source_failed":
            item["warnings_count"] = max(item["warnings_count"], int(counts.get("warnings_count") or 1))
            item["status"] = "failed"
            item["finished_at"] = event.get("timestamp", "")

    for source_index in range(1, source_count + 1):
        items_by_index.setdefault(source_index, _waiting_source_item(source_index, source_count))
        items_by_index[source_index]["source_count"] = source_count

    items = [items_by_index[index] for index in sorted(items_by_index)]
    summary = {
        "total_sources": source_count,
        "sources_completed": sum(1 for item in items if item["status"] in {"completed", "warning"}),
        "sources_failed": sum(1 for item in items if item["status"] == "failed"),
        "sources_running": sum(1 for item in items if item["status"] == "running"),
        "jobs_found_so_far": sum(int(item["jobs_found"] or 0) for item in items),
        "warnings_so_far": sum(int(item["warnings_count"] or 0) for item in items),
        "current_source": next((item["source_name"] for item in items if item["status"] == "running"), ""),
    }
    return {"items": items, "summary": summary}


def _waiting_source_item(source_index: int, source_count: int) -> dict[str, Any]:
    return {
        "source_name": f"Waiting source {source_index}/{source_count}"
        if source_count
        else f"Waiting source {source_index}",
        "source_index": source_index,
        "source_count": source_count,
        "status": "waiting",
        "jobs_found": 0,
        "warnings_count": 0,
        "elapsed_time_seconds": None,
        "latest_message": "",
        "started_at": "",
        "finished_at": "",
    }
