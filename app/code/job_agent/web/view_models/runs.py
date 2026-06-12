from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from job_agent.config import ROOT
from job_agent.llm import LlmService
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
    view_titles = {
        "test": "Test Runs",
        "archived": "Archived Runs",
        "deleted": "Deleted Runs",
    }
    return {"title": view_titles.get(view, "Runs"), "runs": runs, "view": view}


def build_run_detail_view(
    run_id: str,
    category: str = "",
    app_status: str = "",
    source: str = "",
    root: Path = ROOT,
    only_unreviewed: bool = False,
    ai_prioritized: bool = False,
    materials_missing: bool = False,
    match_group: str = "",
) -> dict:
    store = RunStore(root)
    record = store.get(run_id)
    if not record:
        raise KeyError(run_id)
    all_packages = [
        package for package in PackageIndexService(root).list_packages(run_id) if package.get("review_list", True)
    ]
    packages = list(all_packages)
    if category:
        packages = [pkg for pkg in packages if pkg.get("match_category") == category]
    if match_group == "strong_exploratory":
        packages = [pkg for pkg in packages if pkg.get("match_category") in {"strong", "exploratory"}]
    if app_status:
        packages = [pkg for pkg in packages if pkg.get("application_status") == app_status]
    if only_unreviewed:
        packages = [pkg for pkg in packages if pkg.get("application_status") == "unreviewed"]
    if ai_prioritized:
        packages = [pkg for pkg in packages if _truthy(pkg.get("ai_should_prioritize"))]
    if materials_missing:
        packages = [pkg for pkg in packages if _material_status(pkg) == "missing"]
    if source:
        source_search = source.lower()
        packages = [
            pkg
            for pkg in packages
            if source_search in str(pkg.get("source_url", "")).lower()
            or source_search in str(pkg.get("source", "")).lower()
        ]
    packages = build_triage_packages(packages)
    all_events = store.read_events(run_id)
    source_warnings = [event for event in all_events if event.get("event_type") == "source_warning"]
    match_highlights = [event for event in all_events if event.get("event_type") == "match_highlight"]
    ai_evaluation_events = [event for event in all_events if event.get("event_type", "").startswith("ai_evaluation_")]
    source_progress = build_source_progress(all_events)
    finalize_source_progress_for_run(source_progress, record.status)
    run_overview = build_run_overview(record, all_packages, all_events, match_highlights)
    run_progress = build_run_progress(record, source_progress["summary"], all_events, run_overview)
    activity = build_activity_view(all_events)
    return {
        "title": f"Run - {record.started_at[:10] or record.run_id}",
        "run": record,
        "run_overview": run_overview,
        "run_progress": run_progress,
        "packages": packages,
        "events": activity["recent"],
        "activity": activity,
        "source_warnings": source_warnings,
        "match_highlights": match_highlights,
        "ai_evaluation_events": ai_evaluation_events,
        "source_progress": source_progress["items"],
        "source_progress_summary": source_progress["summary"],
        "token_records": TokenUsageStore(root).list_for_run(run_id),
        "llm_configured": LlmService(root).is_configured(),
        "all_run_jobs_url": _jobs_url({"run_id": run_id, "dedupe": "0"}),
        "day_jobs_url": _jobs_url(
            {
                "date_from": record.started_at[:10],
                "date_to": record.started_at[:10],
                "dedupe": "0",
            }
        ),
        "filters": {
            "category": category,
            "app_status": app_status,
            "source": source,
            "only_unreviewed": only_unreviewed,
            "ai_prioritized": ai_prioritized,
            "materials_missing": materials_missing,
            "match_group": match_group,
        },
    }


def _jobs_url(params: dict[str, str]) -> str:
    pairs: list[tuple[str, str]] = [
        *params.items(),
        ("category_include", "strong"),
        ("category_include", "exploratory"),
        ("category_include", "weak"),
        ("category_include", "excluded"),
        ("category_include", "not_scored"),
        ("posting_status_include", "active"),
        ("posting_status_include", "no_longer_posted"),
        ("posting_status_include", "unknown"),
    ]
    return f"/jobs?{urlencode(pairs)}"


def build_run_overview(
    record,
    packages: list[dict[str, Any]],
    events: list[dict[str, Any]],
    match_highlights: list[dict[str, Any]],
) -> dict[str, Any]:
    material_generated = sum(1 for package in packages if _material_status(package) == "generated")
    material_missing = sum(1 for package in packages if _material_status(package) == "missing")
    source_processed = [event for event in events if event.get("event_type") == "source_processed"]
    previously_seen_skipped = sum(
        int((event.get("counts") or {}).get("previously_seen_skipped") or 0) for event in source_processed
    )
    new_changed = sum(
        int((event.get("counts") or {}).get("new_roles") or 0)
        + int((event.get("counts") or {}).get("changed_roles") or 0)
        for event in source_processed
    )
    limit_hit_sources = [
        event.get("current_source") or "Unknown source"
        for event in source_processed
        if int((event.get("counts") or {}).get("listing_limit_skipped_count") or 0) > 0
    ]
    detail_limit_hit_sources = [
        event.get("current_source") or "Unknown source"
        for event in source_processed
        if int((event.get("counts") or {}).get("detail_limit_skipped_count") or 0) > 0
    ]
    return {
        "proposed_jobs": len(packages),
        "materials_generated": material_generated,
        "materials_missing": material_missing,
        "interesting_signals": len(match_highlights),
        "new_changed": new_changed or record.new_roles + record.changed_roles,
        "previously_seen_skipped": previously_seen_skipped,
        "limit_hit_sources": limit_hit_sources,
        "detail_limit_hit_sources": detail_limit_hit_sources,
        "is_running": record.status in {"pending", "running"},
        "option_summary": build_option_summary(record.options),
    }


def build_run_progress(
    record,
    source_summary: dict[str, Any],
    events: list[dict[str, Any]],
    run_overview: dict[str, Any],
) -> dict[str, Any]:
    total_sources = int(source_summary.get("total_sources") or 0)
    completed_sources = int(source_summary.get("sources_completed") or 0)
    failed_sources = int(source_summary.get("sources_failed") or 0)
    deferred_sources = int(source_summary.get("sources_deferred") or 0)
    running_sources = int(source_summary.get("sources_running") or 0)
    waiting_sources = int(source_summary.get("sources_waiting") or 0)
    finished_sources = completed_sources + failed_sources + deferred_sources
    progress_percent = int(source_summary.get("progress_percent") or 0)
    if not progress_percent:
        if record.status == "completed":
            progress_percent = 100
        elif record.status == "failed":
            progress_percent = max(8, int((finished_sources / total_sources) * 100)) if total_sources else 100
        elif total_sources:
            progress_percent = max(8, int((finished_sources / total_sources) * 100))
        else:
            progress_percent = 8 if record.status in {"pending", "running"} else 100

    if record.status == "completed":
        phase = "Completed"
    elif record.status == "failed":
        phase = "Failed"
    elif running_sources:
        phase = "Sources running in parallel"
    elif waiting_sources:
        phase = "Preparing source queue"
    else:
        phase = "Starting daily run"

    latest_event = next((event for event in reversed(events) if str(event.get("message") or "").strip()), {})
    if total_sources:
        checked_summary = f"{finished_sources}/{total_sources} sources finished"
        if deferred_sources:
            checked_summary = f"{checked_summary}; {completed_sources} checked, {deferred_sources} deferred"
        summary = f"{checked_summary}; {running_sources} running, {waiting_sources} waiting."
    else:
        summary = str(latest_event.get("message") or "Preparing the daily run.")

    return {
        "status": record.status,
        "phase": phase,
        "summary": summary,
        "latest_message": str(latest_event.get("message") or ""),
        "progress_percent": max(0, min(100, progress_percent)),
        "total_sources": total_sources,
        "completed_sources": completed_sources,
        "failed_sources": failed_sources,
        "deferred_sources": deferred_sources,
        "running_sources": running_sources,
        "waiting_sources": waiting_sources,
        "finished_sources": finished_sources,
        "jobs_found": int(source_summary.get("jobs_found_so_far") or record.total_loaded or 0),
        "warnings": int(source_summary.get("warnings_so_far") or record.source_warnings or 0),
        "proposed_jobs": int(run_overview.get("proposed_jobs") or 0),
        "interesting_signals": int(run_overview.get("interesting_signals") or 0),
    }


def finalize_source_progress_for_run(source_progress: dict[str, Any], run_status: str) -> None:
    if run_status not in {"completed", "failed"}:
        return
    items = list(source_progress.get("items") or [])
    if not items:
        return
    for item in items:
        if item.get("processed") or item.get("status") == "failed":
            continue
        if item.get("status") not in {"running", "waiting", "completed", "warning"}:
            continue
        item["status"] = "deferred" if run_status == "completed" else "failed"
        item["stage"] = "Deferred" if run_status == "completed" else "Stopped"
        item["progress_percent"] = 100
        if run_status == "completed":
            jobs_found = int(item.get("jobs_found") or 0)
            item["latest_message"] = (
                f"{jobs_found} listing(s) were seen, but this source was left outside today's review pass."
                if jobs_found
                else "This source was left outside today's review pass."
            )
            highlight = {
                "kind": "deferred",
                "label": "Deferred",
                "title": "Left for another pass",
                "detail": item["latest_message"],
                "score": 0,
            }
        else:
            item["latest_message"] = "Run stopped before this source reported final results."
            highlight = {
                "kind": "warning",
                "label": "Stopped",
                "title": item["stage"],
                "detail": item["latest_message"],
                "score": 0,
            }
        item["source_summary_highlight"] = highlight
        item["highlight"] = highlight
        item["highlights"] = [highlight]
    summary = source_progress.setdefault("summary", {})
    summary["sources_completed"] = sum(1 for item in items if item["status"] in {"completed", "warning"})
    summary["sources_failed"] = sum(1 for item in items if item["status"] == "failed")
    summary["sources_deferred"] = sum(1 for item in items if item["status"] == "deferred")
    summary["sources_running"] = 0
    summary["sources_waiting"] = 0
    summary["progress_percent"] = 100
    summary["current_source"] = ""
    summary["active_source_names"] = []


def build_option_summary(options: dict[str, Any]) -> list[str]:
    labels = []
    if options.get("is_test"):
        labels.append("Test run")
    if options.get("include_seen"):
        labels.append("Includes jobs seen before")
    else:
        labels.append("Skips jobs seen before")
    if options.get("include_weak"):
        labels.append("Includes weak matches")
    else:
        labels.append("Strong and exploratory matches only")
    if options.get("generate_materials"):
        labels.append("Generates materials during the run")
    else:
        labels.append("Materials generated after review")
    if options.get("use_llm"):
        labels.append("Claude writing requested")
    if options.get("ai_enhanced_search"):
        labels.append("AI-assisted scoring requested")
    if options.get("mark_seen"):
        labels.append("Marks reviewed jobs as seen")
    detail_limit = options.get("detail_extraction_limit", 25)
    if detail_limit is None:
        labels.append("Reviews all new jobs in detail")
    else:
        labels.append(f"Reviews up to {detail_limit} new jobs in detail")
    if options.get("append_to_daily_run"):
        labels.append("Adds source results to today's daily run")
    return labels


def build_activity_view(events: list[dict[str, Any]]) -> dict[str, Any]:
    groups = {
        "milestones": [],
        "source": [],
        "scoring": [],
        "generation": [],
        "warnings": [],
        "all": list(events),
    }
    for event in events:
        event_type = str(event.get("event_type") or "")
        phase = str(event.get("phase") or "")
        if "warning" in event_type or event.get("status") == "failed":
            groups["warnings"].append(event)
        if event_type in {
            "run_started",
            "profile_loaded",
            "source_started",
            "source_completed",
            "source_processed",
            "jobs_loaded",
            "seen_marked",
            "seen_mark_skipped",
            "run_completed",
            "run_failed",
        }:
            groups["milestones"].append(event)
        if phase in {"source_ingestion", "source_processing"} or event_type.startswith("source_"):
            groups["source"].append(event)
        if phase in {"classification", "scoring"} or event_type in {"job_classified", "job_scored", "match_highlight"}:
            groups["scoring"].append(event)
        if phase == "generation" or event_type.startswith("package_"):
            groups["generation"].append(event)
    return {
        "recent": groups["milestones"][-8:] or events[-8:],
        "groups": groups,
        "counts": {name: len(items) for name, items in groups.items()},
        "sections": [
            {"key": "warnings", "label": "Warnings", "events": groups["warnings"]},
            {"key": "source", "label": "Source activity", "events": groups["source"]},
            {"key": "scoring", "label": "Scoring", "events": groups["scoring"]},
            {"key": "generation", "label": "Generation", "events": groups["generation"]},
            {"key": "all", "label": "All events", "events": groups["all"]},
        ],
    }


def build_triage_packages(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for package in packages:
        enriched_package = dict(package)
        enriched_package.update(build_package_triage(enriched_package))
        enriched.append(enriched_package)
    return sorted(enriched, key=_triage_sort_key, reverse=True)


def build_package_triage(package: dict[str, Any]) -> dict[str, Any]:
    material_status = _material_status(package)
    application_status = str(package.get("application_status") or "unreviewed")
    concerns = _as_list(package.get("concerns"))
    ai_risk_flags = _as_list(package.get("ai_risk_flags"))
    badges = _triage_badges(package, material_status, concerns, ai_risk_flags)

    primary_summary = (
        str(package.get("ai_summary") or "").strip()
        or str(package.get("recommended_angle") or "").strip()
        or "No summary available yet."
    )
    primary_risk = ai_risk_flags[0] if ai_risk_flags else concerns[0] if concerns else "No obvious risk flagged."
    triage_score = _score(package, material_status, application_status)

    return {
        "triage_score": triage_score,
        "triage_badges": badges,
        "primary_summary": primary_summary,
        "primary_risk": primary_risk,
        "primary_action_label": "Regenerate materials" if material_status == "generated" else "Generate materials",
        "material_status": material_status,
    }


def _triage_badges(
    package: dict[str, Any], material_status: str, concerns: list[str], ai_risk_flags: list[str]
) -> list[dict[str, str]]:
    badges: list[dict[str, str]] = []
    category = str(package.get("match_category") or "").lower()
    state = str(package.get("state") or "").lower()
    remote = str(package.get("remote") or "").lower()
    rate = str(package.get("rate") or "").strip()
    source = str(package.get("source") or "").lower()
    source_url = str(package.get("source_url") or "").lower()

    if _truthy(package.get("ai_should_prioritize")):
        badges.append({"label": "Prioritize", "class": "strong"})
    if category == "strong":
        badges.append({"label": "Strong match", "class": "strong"})
    elif category == "exploratory":
        badges.append({"label": "Exploratory", "class": "exploratory"})

    if str(package.get("ai_fit_confidence") or "").lower() == "high":
        badges.append({"label": "AI high confidence", "class": "high"})
    if _contains_any(remote, ["fully remote", "remote", "work from home"]):
        badges.append({"label": "Fully remote", "class": "high"})
    elif _contains_any(remote, ["hybrid", "onsite", "on-site", "office"]):
        badges.append({"label": "Hybrid/onsite", "class": "waiting"})
    if material_status == "generated":
        badges.append({"label": "Materials generated", "class": "generated"})
    else:
        badges.append({"label": "Materials missing", "class": "missing"})
    if state in {"new", "changed"}:
        badges.append({"label": state.replace("_", " ").title(), "class": "high" if state == "new" else "warning"})
    if _has_language_risk(concerns + ai_risk_flags):
        badges.append({"label": "Language risk", "class": "warning"})
    if rate and rate.lower() not in {"not listed", "n/a", "unknown", "none"}:
        badges.append({"label": "Rate listed", "class": "waiting"})
    if "manual" in source or "manual" in source_url:
        badges.append({"label": "Manual intake", "class": "waiting"})

    return badges


def _triage_sort_key(package: dict[str, Any]) -> tuple[int, int, int, int, int, int, int]:
    category = str(package.get("match_category") or "").lower()
    state = str(package.get("state") or "").lower()
    application_status = str(package.get("application_status") or "").lower()
    return (
        1 if _truthy(package.get("ai_should_prioritize")) else 0,
        1 if category == "strong" else 0,
        1 if category == "exploratory" else 0,
        _int_score(package.get("match_score")),
        1 if state in {"new", "changed"} else 0,
        1 if application_status == "unreviewed" else 0,
        1 if package.get("material_status") == "missing" else 0,
    )


def _score(package: dict[str, Any], material_status: str, application_status: str) -> int:
    category = str(package.get("match_category") or "").lower()
    score = _int_score(package.get("match_score"))
    score += 40 if _truthy(package.get("ai_should_prioritize")) else 0
    score += 30 if category == "strong" else 15 if category == "exploratory" else 0
    score += 8 if application_status == "unreviewed" else 0
    score += 3 if material_status == "missing" else 0
    return score


def _material_status(package: dict[str, Any]) -> str:
    status = str(package.get("material_status") or "").strip().lower()
    if status:
        return status
    return "generated" if package.get("materials_generated") else "missing"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _int_score(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _contains_any(value: str, needles: list[str]) -> bool:
    return any(needle in value for needle in needles)


def _has_language_risk(values: list[str]) -> bool:
    return any("language" in value.lower() or "dutch" in value.lower() or "french" in value.lower() for value in values)


def build_source_progress(events: list[dict[str, Any]]) -> dict[str, Any]:
    items_by_index: dict[int, dict[str, Any]] = {}
    source_count = 0

    for event in events:
        if event.get("phase") not in {"source_ingestion", "source_processing"} or not str(
            event.get("event_type", "")
        ).startswith("source_"):
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
            item["stage"] = "Starting"
        elif event_type == "source_warning":
            item["warnings_count"] = max(item["warnings_count"], 0) + int(counts.get("warnings_count") or 1)
        elif event_type == "source_activity":
            item["status"] = "running"
            item["activity_count"] += 1
            item["stage"] = _source_stage(event.get("message", ""))
            item["latest_message"] = _source_activity_summary(event.get("message", ""))
            if int(counts.get("jobs_found") or 0):
                item["jobs_found"] = int(counts.get("jobs_found") or 0)
            if int(counts.get("page_explored_count") or 0):
                item["page_explored_count"] = int(counts.get("page_explored_count") or 0)
            if int(counts.get("page_total") or 0):
                item["page_total"] = int(counts.get("page_total") or 0)
            if int(counts.get("detail_read_count") or 0):
                item["detail_read_count"] = int(counts.get("detail_read_count") or 0)
            if int(counts.get("detail_total") or 0):
                item["detail_total"] = int(counts.get("detail_total") or 0)
            if int(counts.get("detail_fetch_count") or 0):
                item["detail_fetch_count"] = int(counts.get("detail_fetch_count") or 0)
            if int(counts.get("reviewed_in_detail_count") or 0):
                item["reviewed_in_detail_count"] = int(counts.get("reviewed_in_detail_count") or 0)
            item["recent_activity"].append(
                {
                    "timestamp": event.get("timestamp", ""),
                    "message": event.get("message", ""),
                    "stage": item["stage"],
                }
            )
            item["recent_activity"] = item["recent_activity"][-8:]
        elif event_type == "source_completed":
            item["jobs_found"] = int(counts.get("jobs_found") or 0)
            item["warnings_count"] = max(item["warnings_count"], int(counts.get("warnings_count") or 0))
            item["status"] = "warning" if item["warnings_count"] > 0 else "completed"
            item["finished_at"] = event.get("timestamp", "")
            item["stage"] = "Completed"
            item["latest_message"] = (
                event.get("message") or f"{item['jobs_found']} listing(s) seen; preparing review decisions."
            )
        elif event_type == "source_failed":
            item["warnings_count"] = max(item["warnings_count"], int(counts.get("warnings_count") or 1))
            item["status"] = "failed"
            item["finished_at"] = event.get("timestamp", "")
            item["stage"] = "Failed"
        elif event_type == "source_processed":
            item["status"] = "warning" if item["warnings_count"] > 0 else "completed"
            item["processed"] = True
            item["finished_at"] = event.get("timestamp", item.get("finished_at", ""))
            item["stage"] = "Processed"
            item["new_changed"] = int(counts.get("new_roles") or 0) + int(counts.get("changed_roles") or 0)
            item["previously_seen_skipped"] = int(counts.get("previously_seen_skipped") or 0)
            item["strong_matches"] = int(counts.get("strong_matches") or 0)
            item["exploratory_matches"] = int(counts.get("exploratory_matches") or 0)
            item["included_roles"] = int(counts.get("included_roles") or 0)
            item["listing_observed_count"] = int(counts.get("listing_observed_count") or 0)
            item["listing_limit_skipped_count"] = int(counts.get("listing_limit_skipped_count") or 0)
            item["pagination_fetch_count"] = int(counts.get("pagination_fetch_count") or 0)
            item["page_explored_count"] = max(
                int(item.get("page_explored_count") or 0),
                1 + int(counts.get("pagination_fetch_count") or 0),
            )
            item["page_total"] = max(
                int(item.get("page_total") or 0),
                int(item.get("page_explored_count") or 0),
            )
            item["detail_fetch_count"] = int(counts.get("detail_fetch_count") or 0)
            item["detail_enriched_count"] = int(counts.get("detail_enriched_count") or 0)
            item["reviewed_in_detail_count"] = int(counts.get("reviewed_in_detail_count") or 0)
            item["detail_read_count"] = max(
                int(item.get("detail_read_count") or 0),
                int(counts.get("detail_fetch_count") or 0),
            )
            item["detail_total"] = max(
                int(item.get("detail_total") or 0),
                int(counts.get("reviewed_in_detail_count") or 0),
            )
            item["detail_limit_skipped_count"] = int(counts.get("detail_limit_skipped_count") or 0)
            _apply_source_processed_highlight(item)
            item["latest_message"] = _source_processed_pulse(item)

    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type not in {"match_highlight", "ai_evaluation_completed", "ai_evaluation_failed"}:
            continue
        counts = event.get("counts") or {}
        source_index = int(counts.get("source_index") or 0)
        if source_index <= 0 or source_index not in items_by_index:
            continue
        item = items_by_index[source_index]
        if event_type == "match_highlight":
            highlight = {
                "kind": "strong",
                "label": "Interesting finding",
                "title": str(event.get("current_job") or "Promising match"),
                "detail": str(event.get("message") or "A promising role matched the profile."),
                "score": int(counts.get("score") or 0),
            }
            item.setdefault("highlights", []).append(highlight)
            item["highlight"] = highlight
        elif (
            event_type == "ai_evaluation_completed"
            and item["highlight"]["kind"] not in {"strong", "high"}
            and not item.get("highlights")
        ):
            highlight = {
                "kind": "medium",
                "label": "AI review",
                "title": str(event.get("current_job") or "Role reviewed"),
                "detail": str(event.get("message") or "AI relevance review completed."),
                "score": int(counts.get("score") or 0),
            }
            item.setdefault("highlights", []).append(highlight)
            item["highlight"] = highlight
        elif (
            event_type == "ai_evaluation_failed"
            and item["highlight"]["kind"] == "neutral"
            and not item.get("highlights")
        ):
            highlight = {
                "kind": "warning",
                "label": "Review issue",
                "title": str(event.get("current_job") or "AI review failed"),
                "detail": str(event.get("message") or "AI relevance review failed."),
                "score": 0,
            }
            item.setdefault("highlights", []).append(highlight)
            item["highlight"] = highlight

    for source_index in range(1, source_count + 1):
        items_by_index.setdefault(source_index, _waiting_source_item(source_index, source_count))
        items_by_index[source_index]["source_count"] = source_count

    for item in items_by_index.values():
        item["progress_percent"] = _source_progress_percent(item)
        if item["highlight"]["kind"] == "neutral":
            item["highlight"] = _fallback_source_highlight(item)
        base_highlight = item.get("source_summary_highlight") or item["highlight"]
        highlights = list(item.get("highlights") or [])
        if not _has_equivalent_highlight(highlights, base_highlight):
            highlights.append(base_highlight)
        item["highlights"] = highlights
        item["highlight"] = highlights[0]

    items = [items_by_index[index] for index in sorted(items_by_index)]
    completed_count = sum(1 for item in items if item["status"] in {"completed", "warning"})
    failed_count = sum(1 for item in items if item["status"] == "failed")
    deferred_count = sum(1 for item in items if item["status"] == "deferred")
    running_count = sum(1 for item in items if item["status"] == "running")
    waiting_count = sum(1 for item in items if item["status"] == "waiting")
    summary = {
        "total_sources": source_count,
        "sources_completed": completed_count,
        "sources_failed": failed_count,
        "sources_deferred": deferred_count,
        "sources_running": running_count,
        "sources_waiting": waiting_count,
        "jobs_found_so_far": sum(int(item["jobs_found"] or 0) for item in items),
        "warnings_so_far": sum(int(item["warnings_count"] or 0) for item in items),
        "current_source": next((item["source_name"] for item in items if item["status"] == "running"), ""),
        "active_source_names": [item["source_name"] for item in items if item["status"] == "running"][:3],
        "progress_percent": (
            round(sum(int(item["progress_percent"] or 0) for item in items) / len(items)) if items else 0
        ),
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
        "processed": False,
        "stage": "Waiting",
        "activity_count": 0,
        "recent_activity": [],
        "new_changed": 0,
        "previously_seen_skipped": 0,
        "strong_matches": 0,
        "exploratory_matches": 0,
        "included_roles": 0,
        "listing_observed_count": 0,
        "listing_limit_skipped_count": 0,
        "pagination_fetch_count": 0,
        "page_explored_count": 0,
        "page_total": 0,
        "detail_fetch_count": 0,
        "detail_enriched_count": 0,
        "detail_read_count": 0,
        "detail_total": 0,
        "reviewed_in_detail_count": 0,
        "detail_limit_skipped_count": 0,
        "progress_percent": 0,
        "source_summary_highlight": None,
        "highlights": [],
        "highlight": {
            "kind": "neutral",
            "label": "Waiting",
            "title": "Waiting for this source",
            "detail": "This source has not started yet.",
            "score": 0,
        },
    }


def _apply_source_processed_highlight(item: dict[str, Any]) -> None:
    if int(item.get("included_roles") or 0) > 0:
        highlight = {
            "kind": "strong",
            "label": "Potentially exciting",
            "title": f"{item['included_roles']} review pick(s)",
            "detail": (
                f"{item['strong_matches']} strong and {item['exploratory_matches']} exploratory match(es) "
                f"were added to today's review list."
            ),
            "score": 0,
        }
    elif int(item.get("new_changed") or 0) > 0:
        highlight = {
            "kind": "medium",
            "label": "New activity",
            "title": f"{item['new_changed']} fresh or updated listing(s)",
            "detail": "No role crossed the review threshold yet, but this source did bring in fresh listings.",
            "score": 0,
        }
    elif int(item.get("previously_seen_skipped") or 0) > 0:
        highlight = {
            "kind": "neutral",
            "label": "Already seen",
            "title": "No fresh listings",
            "detail": f"{item['previously_seen_skipped']} listing(s) were already known from earlier runs.",
            "score": 0,
        }
    elif int(item.get("warnings_count") or 0) > 0:
        highlight = {
            "kind": "warning",
            "label": "Needs attention",
            "title": f"{item['warnings_count']} warning(s)",
            "detail": str(item.get("latest_message") or "This source completed with warnings."),
            "score": 0,
        }
    else:
        return
    item["highlight"] = highlight
    item["source_summary_highlight"] = highlight


def _fallback_source_highlight(item: dict[str, Any]) -> dict[str, Any]:
    status = str(item.get("status") or "waiting")
    if status == "running":
        return {
            "kind": "medium",
            "label": "Now checking",
            "title": str(item.get("stage") or "Working"),
            "detail": str(item.get("latest_message") or "This source is currently being checked."),
            "score": 0,
        }
    if status == "failed":
        return {
            "kind": "warning",
            "label": "Stopped",
            "title": "Source failed",
            "detail": str(item.get("latest_message") or "This source failed during the run."),
            "score": 0,
        }
    if status in {"completed", "warning"}:
        if int(item.get("jobs_found") or 0) > 0:
            return {
                "kind": "neutral",
                "label": "Checked",
                "title": f"{item['jobs_found']} listing(s) seen",
                "detail": "Nothing was added to today's review list from this source.",
                "score": 0,
            }
        return {
            "kind": "neutral",
            "label": "Checked",
            "title": "No listings found",
            "detail": "This source finished without finding new listings.",
            "score": 0,
        }
    if status == "deferred":
        return {
            "kind": "deferred",
            "label": "Deferred",
            "title": "Left for another pass",
            "detail": str(item.get("latest_message") or "This source was left outside today's review pass."),
            "score": 0,
        }
    return {
        "kind": "neutral",
        "label": "Waiting",
        "title": "Waiting for this source",
        "detail": "This source has not started yet.",
        "score": 0,
    }


def _source_progress_percent(item: dict[str, Any]) -> int:
    status = str(item.get("status") or "waiting")
    if status in {"completed", "warning", "failed", "deferred"}:
        return 100
    if status == "waiting":
        return 0
    detail_total = int(item.get("detail_total") or 0)
    detail_read = int(item.get("detail_read_count") or 0)
    if detail_total > 0:
        return max(35, min(92, round(45 + (detail_read / detail_total) * 45)))
    page_total = int(item.get("page_total") or 0)
    page_explored = int(item.get("page_explored_count") or 0)
    if page_total > 0:
        return max(18, min(82, round((page_explored / page_total) * 75)))
    if int(item.get("jobs_found") or 0) > 0:
        return 42
    return 18


def _source_stage(message: str) -> str:
    lowered = message.lower()
    if "detail" in lowered:
        return "Reading detail pages"
    if "pagination" in lowered:
        return "Reading pagination"
    if "listing" in lowered or "recipe selected" in lowered:
        return "Reading listings"
    if "scored" in lowered or "classified" in lowered:
        return "Scoring jobs"
    return "Working"


def _source_activity_summary(message: str) -> str:
    lowered = message.lower()
    if "detail page" in lowered:
        return "Reading detail pages for fresh listings."
    if "pagination" in lowered:
        return "Checking additional listing pages."
    if "listing page" in lowered or "recipe selected" in lowered:
        return "Reading listing pages for this source."
    if "scored" in lowered or "classified" in lowered:
        return "Scoring fresh listings from this source."
    return "Working through this source."


def _source_processed_pulse(item: dict[str, Any]) -> str:
    review_picks = int(item.get("included_roles") or 0)
    fresh = int(item.get("new_changed") or 0)
    already_seen = int(item.get("previously_seen_skipped") or 0)
    deferred_detail = int(item.get("detail_limit_skipped_count") or 0)
    if deferred_detail:
        return f"Review budget left {deferred_detail} fresh listing(s) for another pass."
    if review_picks:
        return f"Added {review_picks} review pick(s) from this source."
    if fresh:
        return f"Saw {fresh} fresh or updated listing(s); none moved to the review list."
    if already_seen:
        return "No fresh listings; known listings were skipped."
    return "Checked this source; no review picks today."


def _has_equivalent_highlight(highlights: list[dict[str, Any]], candidate: dict[str, Any]) -> bool:
    return any(
        highlight.get("label") == candidate.get("label")
        and highlight.get("title") == candidate.get("title")
        and highlight.get("detail") == candidate.get("detail")
        for highlight in highlights
    )
