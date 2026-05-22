from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from job_agent.config import ROOT
from job_agent.io.json_store import read_json
from job_agent.models import Job, SeenJobRecord
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.sources import adapter_for_source
from job_agent.store import JobStore

DryRunProgressCallback = Callable[[dict], None]


@dataclass
class DryRunJobPreview:
    title: str
    url: str = ""
    source: str = ""
    source_id: str = ""
    location: str = "Not listed"
    remote: str = "Not listed"
    rate: str = "Not listed"
    workload: str = "Not listed"
    posted_date: str = "Not listed"
    start_date: str = "Not listed"
    languages: list[str] = field(default_factory=list)
    description: str = ""
    description_preview: str = ""
    extraction_notes: list[str] = field(default_factory=list)


@dataclass
class SourceDryRunResult:
    source_id: str
    source_name: str = ""
    source_type: str = ""
    source_enabled: bool = False
    forced_disabled: bool = False
    status: str = "not_found"
    job_count: int = 0
    warning_count: int = 0
    warnings: list[str] = field(default_factory=list)
    jobs: list[DryRunJobPreview] = field(default_factory=list)
    recipe_path: str = ""
    recipe_source_name: str = ""
    base_url: str = ""
    mode_used: str = ""
    run_steps: list[dict] = field(default_factory=list)
    pagination_configured: bool = False
    pagination_link_count: int = 0
    pagination_max_pages: int = 1
    pagination_fetch_count: int = 0
    pagination_fetch_attempts: list[str] = field(default_factory=list)
    listing_observed_count: int = 0
    listing_extracted_count: int = 0
    listing_missing_url_count: int = 0
    listing_rejected_count: int = 0
    listing_duplicate_count: int = 0
    listing_limit_skipped_count: int = 0
    listing_pages: list[dict] = field(default_factory=list)
    seen_new_count: int = 0
    seen_changed_count: int = 0
    seen_previously_seen_count: int = 0
    count_explanations: list[str] = field(default_factory=list)
    detail_follow_enabled: bool = False
    detail_fetch_limit: int | None = None
    detail_fetch_count: int = 0
    detail_enriched_count: int = 0
    detail_request_delay_seconds: float = 0.0
    detail_attempts: list[dict] = field(default_factory=list)
    field_checks: list[dict] = field(default_factory=list)
    capability_checks: list[dict] = field(default_factory=list)


class SourceDryRunService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.execution_sources = ExecutionSourceService(root)

    def dry_run(
        self,
        source_id: str,
        *,
        force_disabled: bool = False,
        progress_callback: DryRunProgressCallback | None = None,
    ) -> SourceDryRunResult:
        source_id = source_id.strip()
        source = self.execution_sources.find_by_source_id(source_id)
        if not source:
            return SourceDryRunResult(source_id=source_id, status="not_found")

        source_name = str(source.get("name") or source_id)
        source_type = str(source.get("type") or "")
        enabled = bool(source.get("enabled", True))
        _emit_progress(
            progress_callback,
            "Source resolved",
            "completed",
            f"Resolved {source_name} ({source_type or 'unknown type'}).",
        )
        if not enabled and not force_disabled:
            return SourceDryRunResult(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                source_enabled=False,
                status="disabled",
            )

        try:
            adapter = adapter_for_source(source, self.root)
            _emit_progress(progress_callback, "Source adapter selected", "completed", adapter.__class__.__name__)
            result = adapter.fetch(progress_callback=progress_callback) if progress_callback else adapter.fetch()
        except Exception as exc:
            return SourceDryRunResult(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                source_enabled=enabled,
                forced_disabled=force_disabled and not enabled,
                status="failing",
                warning_count=1,
                warnings=[f"Source adapter failed: {exc}"],
            )

        warnings = [f"{warning.source}: {warning.message}" for warning in result.warnings]
        jobs = [_job_preview(job) for job in result.jobs]
        metadata = result.metadata or {}
        seen_counts = _seen_state_counts(self.root, result.jobs)
        count_explanations = _count_explanations(len(jobs), metadata, seen_counts)
        _emit_progress(
            progress_callback,
            "Source test extracted jobs",
            "completed" if jobs else "warning",
            f"Extracted {len(jobs)} job(s) and {len(warnings)} warning(s).",
        )
        return SourceDryRunResult(
            source_id=source_id,
            source_name=source_name,
            source_type=source_type,
            source_enabled=enabled,
            forced_disabled=force_disabled and not enabled,
            status=_status(jobs, warnings),
            job_count=len(jobs),
            warning_count=len(warnings),
            warnings=warnings,
            jobs=jobs,
            recipe_path=str(metadata.get("recipe_path") or source.get("recipe_path") or ""),
            recipe_source_name=str(metadata.get("recipe_source_name") or ""),
            base_url=str(metadata.get("base_url") or source.get("url") or ""),
            mode_used=str(metadata.get("mode_used") or ""),
            run_steps=list(metadata.get("run_steps") or []),
            pagination_configured=bool(metadata.get("pagination_configured", False)),
            pagination_link_count=_int(metadata.get("pagination_link_count")),
            pagination_max_pages=_int(metadata.get("pagination_max_pages")) or 1,
            pagination_fetch_count=_int(metadata.get("pagination_fetch_count")),
            pagination_fetch_attempts=[str(item) for item in metadata.get("pagination_fetch_attempts") or []],
            listing_observed_count=_int(metadata.get("listing_observed_count")),
            listing_extracted_count=_int(metadata.get("listing_extracted_count")),
            listing_missing_url_count=_int(metadata.get("listing_missing_url_count")),
            listing_rejected_count=_int(metadata.get("listing_rejected_count")),
            listing_duplicate_count=_int(metadata.get("listing_duplicate_count")),
            listing_limit_skipped_count=_int(metadata.get("listing_limit_skipped_count")),
            listing_pages=list(metadata.get("listing_pages") or []),
            seen_new_count=seen_counts["new"],
            seen_changed_count=seen_counts["changed"],
            seen_previously_seen_count=seen_counts["previously_seen"],
            count_explanations=count_explanations,
            detail_follow_enabled=bool(metadata.get("detail_follow_enabled", False)),
            detail_fetch_limit=_optional_int(metadata.get("detail_fetch_limit")),
            detail_fetch_count=_int(metadata.get("detail_fetch_count")),
            detail_enriched_count=_int(metadata.get("detail_enriched_count")),
            detail_request_delay_seconds=float(metadata.get("detail_request_delay_seconds") or 0.0),
            detail_attempts=list(metadata.get("detail_attempts") or []),
            field_checks=list(metadata.get("field_checks") or []),
            capability_checks=list(metadata.get("capability_checks") or []),
        )


def _status(jobs: list[DryRunJobPreview], warnings: list[str]) -> str:
    if jobs and warnings:
        return "warning"
    if jobs:
        return "success"
    if warnings:
        return "failing"
    return "success"


def _job_preview(job: Job) -> DryRunJobPreview:
    description = " ".join(job.description.split())
    return DryRunJobPreview(
        title=job.title,
        url=job.url,
        source=job.source,
        source_id=job.source_id,
        location=job.location,
        remote=job.remote,
        rate=job.rate,
        workload=job.workload,
        posted_date=job.posted_date,
        start_date=job.start_date,
        languages=list(job.languages),
        description=description,
        description_preview=description[:320],
        extraction_notes=list(job.extraction_notes),
    )


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_int(value) -> int | None:
    if value is None:
        return None
    parsed = _int(value)
    return parsed if parsed else None


def _seen_state_counts(root: Path, jobs: list[Job]) -> dict[str, int]:
    records = _load_seen_records(root)
    by_stable = {record.stable_id: record for record in records}
    by_fuzzy = {record.fuzzy_key: record for record in records}
    counts = {"new": 0, "changed": 0, "previously_seen": 0}
    for job in jobs:
        stable_id = JobStore.job_id(job)
        fuzzy_key = JobStore.fuzzy_key(job)
        content_hash = JobStore.content_hash(job)
        existing = by_stable.get(stable_id) or by_fuzzy.get(fuzzy_key)
        if existing is None:
            counts["new"] += 1
        elif existing.content_hash != content_hash:
            counts["changed"] += 1
        else:
            counts["previously_seen"] += 1
    return counts


def _load_seen_records(root: Path) -> list[SeenJobRecord]:
    path = root / "jobs" / "seen_jobs.json"
    if not path.exists():
        return []
    data = read_json(path, [], strict=True)
    if data and isinstance(data[0], str):
        return [
            SeenJobRecord(
                stable_id=item,
                fuzzy_key=item,
                title="Unknown",
                company="Unknown",
                source="Unknown",
                url="",
                first_seen_date="unknown",
                last_seen_date="unknown",
                content_hash="unknown",
                status="previously_seen",
            )
            for item in data
        ]
    return [SeenJobRecord(**item) for item in data]


def _count_explanations(job_count: int, metadata: dict, seen_counts: dict[str, int]) -> list[str]:
    explanations: list[str] = []
    observed = _int(metadata.get("listing_observed_count"))
    missing_url = _int(metadata.get("listing_missing_url_count"))
    rejected = _int(metadata.get("listing_rejected_count"))
    duplicates = _int(metadata.get("listing_duplicate_count"))
    limited = _int(metadata.get("listing_limit_skipped_count"))
    if observed:
        if observed == job_count and not any([missing_url, rejected, duplicates, limited]):
            explanations.append(f"Observed {observed} listing card(s) and retained all {job_count} as jobs.")
        else:
            reasons = []
            if missing_url:
                reasons.append(f"{missing_url} card(s) had no recipe-readable job URL")
            if rejected:
                reasons.append(f"{rejected} card(s) were rejected by recipe filters")
            if duplicates:
                reasons.append(f"{duplicates} duplicate URL(s) were ignored")
            if limited:
                reasons.append(f"{limited} card(s) were outside the configured run limit")
            reason_text = "; ".join(reasons) if reasons else "some cards did not produce retained jobs"
            explanations.append(
                f"Observed {observed} listing card(s) and retained {job_count} job(s): {reason_text}."
            )
    previously_seen = seen_counts.get("previously_seen", 0)
    changed = seen_counts.get("changed", 0)
    new = seen_counts.get("new", 0)
    if job_count:
        explanations.append(
            "Seen-state check: "
            f"{new} new, {changed} changed, {previously_seen} already seen in previous runs. "
            "This source test does not skip or mark seen jobs; a normal run with Include seen off only processes new/changed jobs."
        )
    return explanations


def _emit_progress(
    callback: DryRunProgressCallback | None,
    phase: str,
    status: str,
    detail: str,
    capability: str = "",
) -> None:
    if callback:
        callback({"phase": phase, "status": status, "detail": detail, "capability": capability})
