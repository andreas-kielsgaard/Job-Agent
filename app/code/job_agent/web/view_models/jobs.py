from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from job_agent.application_status_store import APPLICATION_STATUSES, ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.llm import LlmService
from job_agent.models import SeenJobRecord
from job_agent.run_store import RunRecord, RunStore
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.package_index_service import PackageIndexService
from job_agent.services.source_listing_index_store import SourceListingIndexRecord, SourceListingIndexStore
from job_agent.services.source_registry_service import SourceRegistryService
from job_agent.store import JobStore
from job_agent.web.formatting import markdown_to_html


def build_jobs_view(filters: dict[str, Any] | None = None, root: Path = ROOT) -> dict:
    filters = filters or {}
    service = PackageIndexService(root)
    run_labels = _run_label_map(root)
    all_packages = [_with_run_context(service, job, run_labels) for job in service.list_packages()]
    all_jobs = _with_indexed_listing_jobs(root, all_packages)
    jobs = _filter_jobs(all_jobs, filters)
    if filters.get("dedupe", True):
        jobs = _dedupe_latest(jobs)
    source_options = _source_options(root, jobs, all_jobs)
    run_options = _run_options(root, jobs, all_jobs)
    normalized_filters = _normalized_filters(filters)
    return {
        "title": "Jobs",
        "jobs": jobs,
        "filters": {
            "app_statuses": normalized_filters["app_status_includes"],
            "app_status_includes": normalized_filters["app_status_includes"],
            "app_status_excludes": normalized_filters["app_status_excludes"],
            "categories": normalized_filters["category_includes"],
            "category_includes": normalized_filters["category_includes"],
            "category_excludes": normalized_filters["category_excludes"],
            "source_ids": normalized_filters["source_id_includes"],
            "source_id_includes": normalized_filters["source_id_includes"],
            "source_id_excludes": normalized_filters["source_id_excludes"],
            "run_ids": normalized_filters["run_id_includes"],
            "run_id_includes": normalized_filters["run_id_includes"],
            "run_id_excludes": normalized_filters["run_id_excludes"],
            "date_from": str(filters.get("date_from") or ""),
            "date_to": str(filters.get("date_to") or ""),
            "source": str(filters.get("source") or ""),
            "material_statuses": normalized_filters["material_status_includes"],
            "material_status_includes": normalized_filters["material_status_includes"],
            "material_status_excludes": normalized_filters["material_status_excludes"],
            "posting_status_includes": normalized_filters["posting_status_includes"],
            "posting_status_excludes": normalized_filters["posting_status_excludes"],
            "ai_prioritized": bool(filters.get("ai_prioritized")),
            "dedupe": bool(filters.get("dedupe", True)),
        },
        "source_options": source_options,
        "run_options": run_options,
        "result_count": len(jobs),
    }


def build_job_detail_view(job_id: str, run_id: str = "", root: Path = ROOT) -> dict:
    service = PackageIndexService(root)
    package = service.find_package(job_id, run_id)
    if not package:
        raise KeyError(job_id)
    files = service.read_package_files(package)
    status = ApplicationStatusStore(root).get(job_id)
    return {
        "title": f"Job - {package.get('title') or package.get('stable_id') or 'Detail'}",
        "package": package,
        "files": files,
        "status": status,
        "statuses": sorted(APPLICATION_STATUSES),
        "render_md": markdown_to_html,
        "cv_reference": CvReferenceService(root).get_cv_reference(),
        "score_details": _score_details(package),
        "llm_configured": LlmService(root).is_configured(),
    }


def _score_details(package: dict[str, Any]) -> dict[str, Any]:
    raw_components = package.get("components") or package.get("match_components") or {}
    components: list[dict[str, Any]] = []
    if isinstance(raw_components, dict):
        components = [
            {
                "label": str(key).replace("_", " ").title(),
                "value": value,
                "tone": "positive" if _numeric(value) > 0 else "negative" if _numeric(value) < 0 else "neutral",
            }
            for key, value in raw_components.items()
            if _numeric(value) != 0
        ]
    elif isinstance(raw_components, list):
        components = [item for item in raw_components if isinstance(item, dict)]
    return {
        "components": components,
        "reasons": _text_list(package.get("reasons") or package.get("match_reasons")),
        "concerns": _text_list(package.get("concerns") or package.get("match_concerns")),
        "missing_information": _text_list(package.get("missing_information") or package.get("match_missing_info")),
    }


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(";") if item.strip()]
    return []


def _numeric(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _filter_jobs(jobs: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = _normalized_filters(filters)
    app_status_includes = set(normalized["app_status_includes"])
    app_status_excludes = set(normalized["app_status_excludes"])
    category_includes = set(normalized["category_includes"])
    category_excludes = set(normalized["category_excludes"])
    source_id_includes = set(normalized["source_id_includes"])
    source_id_excludes = set(normalized["source_id_excludes"])
    run_id_includes = set(normalized["run_id_includes"])
    run_id_excludes = set(normalized["run_id_excludes"])
    material_status_includes = set(normalized["material_status_includes"])
    material_status_excludes = set(normalized["material_status_excludes"])
    posting_status_includes = set(normalized["posting_status_includes"])
    posting_status_excludes = set(normalized["posting_status_excludes"])
    source_text = str(filters.get("source") or "").strip().lower()
    date_from = _date(filters.get("date_from"))
    date_to = _date(filters.get("date_to"))

    result = []
    for job in jobs:
        if not _matches_tri_filter(job.get("application_status"), app_status_includes, app_status_excludes):
            continue
        if not _matches_tri_filter(job.get("match_category"), category_includes, category_excludes):
            continue
        if not _matches_tri_filter(job.get("source_id"), source_id_includes, source_id_excludes):
            continue
        if not _matches_tri_filter(job.get("run_id"), run_id_includes, run_id_excludes):
            continue
        if not _matches_tri_filter(job.get("material_status"), material_status_includes, material_status_excludes):
            continue
        if not _matches_tri_filter(job.get("posting_status"), posting_status_includes, posting_status_excludes):
            continue
        if filters.get("ai_prioritized") and not _truthy(job.get("ai_should_prioritize")):
            continue
        if (
            source_text
            and source_text
            not in " ".join(
                [str(job.get("source") or ""), str(job.get("source_id") or ""), str(job.get("source_url") or "")]
            ).lower()
        ):
            continue
        run_date = _date(job.get("run_date"))
        if date_from and (not run_date or run_date < date_from):
            continue
        if date_to and (not run_date or run_date > date_to):
            continue
        result.append(job)
    return result


def _normalized_filters(filters: dict[str, Any]) -> dict[str, list[str]]:
    category_includes = _values(filters, "category_includes", "categories")
    category_excludes = _values(filters, "category_excludes")
    posting_status_includes = _values(filters, "posting_status_includes")
    posting_status_excludes = _values(filters, "posting_status_excludes")
    if not category_includes and not category_excludes:
        category_excludes = ["weak", "excluded"]
    if not posting_status_includes and not posting_status_excludes:
        posting_status_excludes = ["no_longer_posted"]
    return {
        "app_status_includes": _values(filters, "app_status_includes", "app_statuses"),
        "app_status_excludes": _values(filters, "app_status_excludes"),
        "category_includes": category_includes,
        "category_excludes": category_excludes,
        "source_id_includes": _values(filters, "source_id_includes", "source_ids"),
        "source_id_excludes": _values(filters, "source_id_excludes"),
        "run_id_includes": _values(filters, "run_id_includes", "run_ids"),
        "run_id_excludes": _values(filters, "run_id_excludes"),
        "material_status_includes": _values(filters, "material_status_includes", "material_statuses"),
        "material_status_excludes": _values(filters, "material_status_excludes"),
        "posting_status_includes": posting_status_includes,
        "posting_status_excludes": posting_status_excludes,
    }


def _values(filters: dict[str, Any], *keys: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for key in keys:
        value = filters.get(key)
        items = value if isinstance(value, list) else [value] if value else []
        for item in items:
            text = str(item or "").strip()
            if text and text not in seen:
                result.append(text)
                seen.add(text)
    return result


def _matches_tri_filter(value: Any, includes: set[str], excludes: set[str]) -> bool:
    text = str(value or "")
    if includes and text not in includes:
        return False
    return text not in excludes


def _with_run_context(service: PackageIndexService, job: dict[str, Any], run_labels: dict[str, str]) -> dict[str, Any]:
    enriched = dict(job)
    run_id = str(enriched.get("run_id") or "")
    enriched["run_date"] = str(service.infer_package_date(job))
    enriched["run_label"] = run_labels.get(run_id) or (f"Daily Run {enriched['run_date']}" if run_id else "")
    enriched["has_package"] = True
    enriched["can_update_status"] = True
    enriched["posting_status"] = str(enriched.get("posting_status") or "active")
    enriched["match_category"] = str(enriched.get("match_category") or "not_scored")
    enriched["application_status"] = str(enriched.get("application_status") or "unreviewed")
    enriched["source_url"] = str(
        enriched.get("source_url") or enriched.get("url") or enriched.get("application_url") or ""
    )
    return enriched


def _with_indexed_listing_jobs(root: Path, package_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    package_listing_keys = {str(job.get("listing_key") or "") for job in package_jobs if job.get("listing_key")}
    package_stable_ids = {str(job.get("stable_id") or "") for job in package_jobs if job.get("stable_id")}
    try:
        seen_records = JobStore(root, create=False).list_seen_records()
    except ValueError:
        seen_records = []
    seen_by_listing = {record.listing_key: record for record in seen_records if record.listing_key}
    statuses = _application_statuses(root)
    source_lookup = _source_lookup(root)
    rows = list(package_jobs)

    indexed_listing_keys: set[str] = set()
    for summary in SourceListingIndexStore(root).list_all():
        for listing in summary.listings:
            indexed_listing_keys.add(listing.listing_key)
            if listing.listing_key in package_listing_keys:
                continue
            rows.append(
                _listing_job_row(
                    listing,
                    seen_by_listing.get(listing.listing_key),
                    statuses,
                    source_lookup,
                )
            )

    for record in seen_records:
        if record.posting_status != "no_longer_posted":
            continue
        if record.listing_key in package_listing_keys or record.listing_key in indexed_listing_keys:
            continue
        if record.stable_id in package_stable_ids:
            continue
        rows.append(_seen_history_row(record, statuses, source_lookup))
    return rows


def _listing_job_row(
    listing: SourceListingIndexRecord,
    seen: SeenJobRecord | None,
    statuses: dict[str, str],
    source_lookup: dict[str, dict[str, str]],
) -> dict[str, Any]:
    stable_id = seen.stable_id if seen else f"listing-{listing.listing_key}"
    source_id = listing.source_id or _source_id_for(listing.source, listing.url, source_lookup)
    posting_status = seen.posting_status if seen else listing.posting_status
    return {
        "stable_id": stable_id,
        "package_id": stable_id,
        "listing_key": listing.listing_key,
        "run_id": "",
        "run_label": "Indexed listing",
        "run_date": str(listing.last_indexed_at or "")[:10],
        "title": listing.title or (seen.title if seen else "Unknown posting"),
        "company": seen.company if seen else "Unknown",
        "recruiter": "",
        "source": listing.source,
        "source_id": source_id,
        "source_url": listing.url,
        "application_url": listing.url,
        "url": listing.url,
        "location": "Not listed",
        "remote": "Not listed",
        "match_score": 0,
        "match_category": "not_scored",
        "state": "detail reviewed" if seen else "indexed only",
        "application_status": statuses.get(stable_id, "unreviewed"),
        "posting_status": posting_status or "active",
        "material_status": "missing",
        "ai_should_prioritize": False,
        "has_package": False,
        "can_update_status": bool(seen and stable_id in statuses),
    }


def _seen_history_row(
    record: SeenJobRecord, statuses: dict[str, str], source_lookup: dict[str, dict[str, str]]
) -> dict[str, Any]:
    source_id = _source_id_for(record.source, record.url, source_lookup)
    return {
        "stable_id": record.stable_id,
        "package_id": record.stable_id,
        "listing_key": record.listing_key,
        "run_id": "",
        "run_label": "Historical posting",
        "run_date": str(record.last_seen_date or "")[:10],
        "title": record.title,
        "company": record.company,
        "recruiter": "",
        "source": record.source,
        "source_id": source_id,
        "source_url": record.url,
        "application_url": record.url,
        "url": record.url,
        "location": "Not listed",
        "remote": "Not listed",
        "match_score": 0,
        "match_category": "not_scored",
        "state": "historical",
        "application_status": statuses.get(record.stable_id, "unreviewed"),
        "posting_status": record.posting_status or "no_longer_posted",
        "material_status": "missing",
        "ai_should_prioritize": False,
        "has_package": False,
        "can_update_status": record.stable_id in statuses,
    }


def _dedupe_latest(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for job in jobs:
        stable_id = str(job.get("listing_key") or job.get("stable_id") or job.get("package_id") or "")
        existing = by_id.get(stable_id)
        if not stable_id:
            continue
        if not existing or (str(job.get("run_date") or ""), str(job.get("run_id") or "")) > (
            str(existing.get("run_date") or ""),
            str(existing.get("run_id") or ""),
        ):
            by_id[stable_id] = job
    return sorted(by_id.values(), key=lambda item: (item.get("match_score", 0), item.get("title", "")), reverse=True)


def _source_options(
    root: Path, filtered_jobs: list[dict[str, Any]], all_jobs: list[dict[str, Any]]
) -> list[dict[str, str]]:
    options = {source.id: source.name for source in SourceRegistryService(root).list_sources()}
    for job in [*all_jobs, *filtered_jobs]:
        source_id = str(job.get("source_id") or "").strip()
        if not source_id:
            continue
        options[source_id] = str(job.get("source") or source_id)
    return [{"id": key, "label": label} for key, label in sorted(options.items(), key=lambda item: item[1].lower())]


def _run_options(
    root: Path, filtered_jobs: list[dict[str, Any]], all_jobs: list[dict[str, Any]]
) -> list[dict[str, str]]:
    labels = _run_label_map(root)
    by_run: dict[str, str] = {}
    for job in [*all_jobs, *filtered_jobs]:
        run_id = str(job.get("run_id") or "")
        if not run_id:
            continue
        by_run[run_id] = str(job.get("run_label") or labels.get(run_id) or f"Daily Run {job.get('run_date') or run_id}")
    return [
        {"id": run_id, "label": label}
        for run_id, label in sorted(by_run.items(), key=lambda item: item[1].lower(), reverse=True)
    ]


def _run_label_map(root: Path) -> dict[str, str]:
    try:
        records = RunStore(root).list_runs(include_archived=True, include_deleted=True, include_tests=True)
    except ValueError:
        records = []
    return {record.run_id: _run_label(record) for record in records}


def _run_label(record: RunRecord) -> str:
    date_text = str(record.started_at or record.run_id)[:10]
    time_text = str(record.started_at or "")[11:16]
    suffix = f" {time_text}" if time_text else ""
    options = record.options if isinstance(record.options, dict) else {}
    if record.is_test or options.get("is_test"):
        prefix = "Test Run"
    elif options.get("append_to_daily_run"):
        prefix = "Source Ingestion Run"
    else:
        prefix = "Daily Run"
    return f"{prefix} {date_text}{suffix}"


def _application_statuses(root: Path) -> dict[str, str]:
    try:
        return {record.stable_id: record.status for record in ApplicationStatusStore(root).list_all()}
    except ValueError:
        return {}


def _source_lookup(root: Path) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for source in SourceRegistryService(root).list_sources():
        item = {"id": source.id, "name": source.name, "url": source.url}
        lookup[source.id.strip().lower()] = item
        lookup[source.name.strip().lower()] = item
    return lookup


def _source_id_for(source_name: str, url: str, lookup: dict[str, dict[str, str]]) -> str:
    key = str(source_name or "").strip().lower()
    if key in lookup:
        return lookup[key]["id"]
    for item in lookup.values():
        if item["url"] and url and _same_host_path(item["url"], url):
            return item["id"]
    return key


def _same_host_path(left: str, right: str) -> bool:
    from urllib.parse import urlparse

    left_parsed = urlparse(left if "://" in left else f"https://{left}")
    right_parsed = urlparse(right if "://" in right else f"https://{right}")
    left_host = left_parsed.netloc.lower().removeprefix("www.")
    right_host = right_parsed.netloc.lower().removeprefix("www.")
    if not left_host or left_host != right_host:
        return False
    left_path = left_parsed.path.rstrip("/")
    right_path = right_parsed.path.rstrip("/")
    return not left_path or right_path == left_path or right_path.startswith(f"{left_path}/")


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}
