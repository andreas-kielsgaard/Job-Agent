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
    all_packages = PackageIndexService(root).list_packages(run_id)
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
    run_overview = build_run_overview(record, all_packages, all_events, match_highlights)
    activity = build_activity_view(all_events)
    return {
        "run": record,
        "run_overview": run_overview,
        "packages": packages,
        "events": activity["recent"],
        "activity": activity,
        "source_warnings": source_warnings,
        "match_highlights": match_highlights,
        "ai_evaluation_events": ai_evaluation_events,
        "source_progress": source_progress["items"],
        "source_progress_summary": source_progress["summary"],
        "token_records": TokenUsageStore(root).list_for_run(run_id),
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


def build_run_overview(
    record,
    packages: list[dict[str, Any]],
    events: list[dict[str, Any]],
    match_highlights: list[dict[str, Any]],
) -> dict[str, Any]:
    material_generated = sum(1 for package in packages if _material_status(package) == "generated")
    material_missing = sum(1 for package in packages if _material_status(package) == "missing")
    source_processed = [event for event in events if event.get("event_type") == "source_processed"]
    previously_seen_skipped = sum(int((event.get("counts") or {}).get("previously_seen_skipped") or 0) for event in source_processed)
    new_changed = sum(
        int((event.get("counts") or {}).get("new_roles") or 0) + int((event.get("counts") or {}).get("changed_roles") or 0)
        for event in source_processed
    )
    limit_hit_sources = [
        event.get("current_source") or "Unknown source"
        for event in source_processed
        if int((event.get("counts") or {}).get("listing_limit_skipped_count") or 0) > 0
    ]
    return {
        "proposed_jobs": len(packages),
        "materials_generated": material_generated,
        "materials_missing": material_missing,
        "interesting_signals": len(match_highlights),
        "new_changed": new_changed or record.new_roles + record.changed_roles,
        "previously_seen_skipped": previously_seen_skipped,
        "limit_hit_sources": limit_hit_sources,
        "is_running": record.status in {"pending", "running"},
        "option_summary": build_option_summary(record.options),
    }


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
        labels.append("Claude writing enabled")
    if options.get("ai_enhanced_search"):
        labels.append("AI-assisted scoring enabled")
    if options.get("mark_seen"):
        labels.append("Marks scanned jobs as seen")
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
        elif event_type == "source_failed":
            item["warnings_count"] = max(item["warnings_count"], int(counts.get("warnings_count") or 1))
            item["status"] = "failed"
            item["finished_at"] = event.get("timestamp", "")
            item["stage"] = "Failed"
        elif event_type == "source_processed":
            item["new_changed"] = int(counts.get("new_roles") or 0) + int(counts.get("changed_roles") or 0)
            item["previously_seen_skipped"] = int(counts.get("previously_seen_skipped") or 0)
            item["strong_matches"] = int(counts.get("strong_matches") or 0)
            item["exploratory_matches"] = int(counts.get("exploratory_matches") or 0)
            item["included_roles"] = int(counts.get("included_roles") or 0)
            item["listing_observed_count"] = int(counts.get("listing_observed_count") or 0)
            item["listing_limit_skipped_count"] = int(counts.get("listing_limit_skipped_count") or 0)
            item["pagination_fetch_count"] = int(counts.get("pagination_fetch_count") or 0)
            item["detail_fetch_count"] = int(counts.get("detail_fetch_count") or 0)
            item["detail_enriched_count"] = int(counts.get("detail_enriched_count") or 0)

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
        "detail_fetch_count": 0,
        "detail_enriched_count": 0,
    }


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
