from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from inspect import Parameter, signature
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.io.json_store import read_json
from job_agent.models import Job, SeenJobRecord
from job_agent.paths import runtime_jobs_dir
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.extraction_assessment import listing_count_explanations, seen_state_explanation
from job_agent.services.source_test_log_service import SourceTestMaterialLog
from job_agent.sources import SourceFetchOptions, adapter_for_source
from job_agent.store import JobStore

SourceTestProgressCallback = Callable[[dict], None]
SOURCE_TEST_DETAIL_PAGE_LIMIT = 5
SOURCE_TEST_DETAIL_LISTING_PAGE_SAMPLE_TARGET = 2


@dataclass
class SourceTestJobPreview:
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
class SourceTestResult:
    source_id: str
    source_name: str = ""
    source_type: str = ""
    source_enabled: bool = False
    forced_disabled: bool = False
    status: str = "not_found"
    job_count: int = 0
    warning_count: int = 0
    warnings: list[str] = field(default_factory=list)
    jobs: list[SourceTestJobPreview] = field(default_factory=list)
    recipe_path: str = ""
    recipe_source_name: str = ""
    base_url: str = ""
    mode_used: str = ""
    access_strategy: str = ""
    api_request_count: int = 0
    records_observed_count: int = 0
    json_records_extracted_count: int = 0
    run_steps: list[dict] = field(default_factory=list)
    pagination_configured: bool = False
    pagination_strategy: str = ""
    pagination_ajax_url_template_present: bool = False
    pagination_click_selector_configured: bool = False
    pagination_link_count: int = 0
    pagination_max_pages: int = 1
    pagination_fetch_count: int = 0
    pagination_fetch_attempts: list[str] = field(default_factory=list)
    pagination_duplicate_page_count: int = 0
    pagination_duplicate_ratio: float = 0.0
    pagination_unique_jobs_from_fetched_pages: int = 0
    interactive_pagination_control_count: int = 0
    source_access_requires_session: bool = False
    source_access_session_used: bool = False
    source_access_session_scope: str = ""
    source_access_setup_hint: str = ""
    source_access_session_status: str = ""
    source_access_session_label: str = ""
    source_access_login_gate_detected: bool = False
    listing_observed_count: int = 0
    listing_extracted_count: int = 0
    listing_missing_url_count: int = 0
    listing_rejected_count: int = 0
    listing_duplicate_count: int = 0
    listing_limit_skipped_count: int = 0
    visible_total_job_count: int = 0
    listing_pages: list[dict] = field(default_factory=list)
    seen_new_count: int = 0
    seen_changed_count: int = 0
    seen_previously_seen_count: int = 0
    count_explanations: list[str] = field(default_factory=list)
    detail_follow_enabled: bool = False
    detail_fetch_limit: int | None = None
    detail_fetch_count: int = 0
    detail_enriched_count: int = 0
    detail_listing_page_sample_target: int = 0
    detail_verified_listing_page_count: int = 0
    detail_request_delay_seconds: float = 0.0
    detail_attempts: list[dict] = field(default_factory=list)
    detail_description_present_count: int = 0
    detail_description_distinct_count: int = 0
    detail_average_description_length: int = 0
    detail_quality_status: str = ""
    detail_quality_summary: str = ""
    field_checks: list[dict] = field(default_factory=list)
    capability_checks: list[dict] = field(default_factory=list)
    log_dir: str = ""
    log_manifest_path: str = ""


class SourceTestService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.execution_sources = ExecutionSourceService(root)

    def run_test(
        self,
        source_id: str,
        *,
        force_disabled: bool = False,
        progress_callback: SourceTestProgressCallback | None = None,
        export_log: bool = True,
    ) -> SourceTestResult:
        source_id = source_id.strip()
        source = self.execution_sources.find_by_source_id(source_id)
        if not source:
            return SourceTestResult(source_id=source_id, status="not_found")

        material_log = SourceTestMaterialLog(self.root, source_id) if export_log else None
        if material_log:
            material_log.record_source(source)
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
            disabled_result = SourceTestResult(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                source_enabled=False,
                status="disabled",
            )
            _finalize_material_log(material_log, disabled_result)
            return disabled_result

        try:
            adapter = adapter_for_source(source, self.root)
            _emit_progress(progress_callback, "Source adapter selected", "completed", adapter.__class__.__name__)
            result = _fetch_source_test_adapter(adapter, progress_callback, material_log=material_log)
        except Exception as exc:
            failed_result = SourceTestResult(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                source_enabled=enabled,
                forced_disabled=force_disabled and not enabled,
                status="failing",
                warning_count=1,
                warnings=[f"Source adapter failed: {exc}"],
            )
            _finalize_material_log(material_log, failed_result)
            return failed_result

        warnings = [f"{warning.source}: {warning.message}" for warning in result.warnings]
        jobs = [_job_preview(job) for job in result.jobs]
        metadata = result.metadata or {}
        seen_counts = _seen_state_counts(self.root, result.jobs)
        count_explanations = _count_explanations(len(jobs), metadata, seen_counts)
        detail_follow_enabled = bool(metadata.get("detail_follow_enabled", False))
        detail_quality = _detail_quality(jobs, detail_follow_enabled=detail_follow_enabled)
        capability_checks = list(metadata.get("capability_checks") or [])
        detail_quality_check = _detail_quality_capability_check(
            detail_quality, detail_follow_enabled=detail_follow_enabled
        )
        if detail_quality_check:
            capability_checks.append(detail_quality_check)
        _emit_progress(
            progress_callback,
            "Source test extracted jobs",
            "completed" if jobs else "warning",
            f"Extracted {len(jobs)} job(s) and {len(warnings)} warning(s).",
        )
        source_test_result = SourceTestResult(
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
            access_strategy=str(metadata.get("access_strategy") or ""),
            api_request_count=_int(metadata.get("api_request_count")),
            records_observed_count=_int(metadata.get("records_observed_count")),
            json_records_extracted_count=_int(metadata.get("json_records_extracted_count")),
            run_steps=list(metadata.get("run_steps") or []),
            pagination_configured=bool(metadata.get("pagination_configured", False)),
            pagination_strategy=str(metadata.get("pagination_strategy") or ""),
            pagination_ajax_url_template_present=bool(metadata.get("pagination_ajax_url_template_present", False)),
            pagination_click_selector_configured=bool(metadata.get("pagination_click_selector_configured", False)),
            pagination_link_count=_int(metadata.get("pagination_link_count")),
            pagination_max_pages=_int(metadata.get("pagination_max_pages")) or 1,
            pagination_fetch_count=_int(metadata.get("pagination_fetch_count")),
            pagination_fetch_attempts=[str(item) for item in metadata.get("pagination_fetch_attempts") or []],
            pagination_duplicate_page_count=_int(metadata.get("pagination_duplicate_page_count")),
            pagination_duplicate_ratio=float(metadata.get("pagination_duplicate_ratio") or 0.0),
            pagination_unique_jobs_from_fetched_pages=_int(metadata.get("pagination_unique_jobs_from_fetched_pages")),
            interactive_pagination_control_count=_int(metadata.get("interactive_pagination_control_count")),
            source_access_requires_session=bool(metadata.get("source_access_requires_session", False)),
            source_access_session_used=bool(metadata.get("source_access_session_used", False)),
            source_access_session_scope=str(metadata.get("source_access_session_scope") or ""),
            source_access_setup_hint=str(metadata.get("source_access_setup_hint") or ""),
            source_access_session_status=str(metadata.get("source_access_session_status") or ""),
            source_access_session_label=str(metadata.get("source_access_session_label") or ""),
            source_access_login_gate_detected=bool(metadata.get("source_access_login_gate_detected", False)),
            listing_observed_count=_int(metadata.get("listing_observed_count")),
            listing_extracted_count=_int(metadata.get("listing_extracted_count")),
            listing_missing_url_count=_int(metadata.get("listing_missing_url_count")),
            listing_rejected_count=_int(metadata.get("listing_rejected_count")),
            listing_duplicate_count=_int(metadata.get("listing_duplicate_count")),
            listing_limit_skipped_count=_int(metadata.get("listing_limit_skipped_count")),
            visible_total_job_count=_int(metadata.get("visible_total_job_count")),
            listing_pages=list(metadata.get("listing_pages") or []),
            seen_new_count=seen_counts["new"],
            seen_changed_count=seen_counts["changed"],
            seen_previously_seen_count=seen_counts["previously_seen"],
            count_explanations=count_explanations,
            detail_follow_enabled=detail_follow_enabled,
            detail_fetch_limit=_optional_int(metadata.get("detail_fetch_limit")),
            detail_fetch_count=_int(metadata.get("detail_fetch_count")),
            detail_enriched_count=_int(metadata.get("detail_enriched_count")),
            detail_listing_page_sample_target=_int(metadata.get("detail_listing_page_sample_target")),
            detail_verified_listing_page_count=_int(metadata.get("detail_verified_listing_page_count")),
            detail_request_delay_seconds=float(metadata.get("detail_request_delay_seconds") or 0.0),
            detail_attempts=list(metadata.get("detail_attempts") or []),
            detail_description_present_count=detail_quality["present_count"],
            detail_description_distinct_count=detail_quality["distinct_count"],
            detail_average_description_length=detail_quality["average_length"],
            detail_quality_status=detail_quality["status"],
            detail_quality_summary=detail_quality["summary"],
            field_checks=list(metadata.get("field_checks") or []),
            capability_checks=capability_checks,
        )
        _finalize_material_log(material_log, source_test_result, source_run_metadata=metadata)
        return source_test_result

    def dry_run(
        self,
        source_id: str,
        *,
        force_disabled: bool = False,
        progress_callback: SourceTestProgressCallback | None = None,
    ) -> SourceTestResult:
        return self.run_test(
            source_id,
            force_disabled=force_disabled,
            progress_callback=progress_callback,
        )


def _status(jobs: list[SourceTestJobPreview], warnings: list[str]) -> str:
    if jobs and warnings:
        return "warning"
    if jobs:
        return "success"
    if warnings:
        return "failing"
    return "success"


def _job_preview(job: Job) -> SourceTestJobPreview:
    description = " ".join(job.description.split())
    return SourceTestJobPreview(
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


def _detail_quality(jobs: list[SourceTestJobPreview], *, detail_follow_enabled: bool) -> dict[str, Any]:
    if not detail_follow_enabled:
        return {
            "status": "not_expected",
            "summary": "Recipe does not request job detail pages.",
            "present_count": 0,
            "distinct_count": 0,
            "average_length": 0,
        }
    descriptions = [(job, " ".join(job.description.split())) for job in jobs if job.description.strip()]
    if not descriptions:
        return {
            "status": "missing",
            "summary": "Detail pages were requested, but no job descriptions were present.",
            "present_count": 0,
            "distinct_count": 0,
            "average_length": 0,
        }
    meaningful = [(job, description) for job, description in descriptions if len(description) >= 40]
    distinct = [
        (job, description) for job, description in meaningful if not _description_is_title_like(description, job.title)
    ]
    average_length = round(sum(len(description) for _job, description in descriptions) / len(descriptions))
    if not distinct:
        return {
            "status": "headline_only",
            "summary": (
                "Detail pages were requested, but descriptions look like headlines or very short listing snippets."
            ),
            "present_count": len(meaningful),
            "distinct_count": 0,
            "average_length": average_length,
        }
    if average_length < 120:
        return {
            "status": "weak",
            "summary": f"Detail descriptions were distinct but short on average ({average_length} characters).",
            "present_count": len(meaningful),
            "distinct_count": len(distinct),
            "average_length": average_length,
        }
    return {
        "status": "good",
        "summary": f"Detail descriptions look distinct from headlines ({average_length} average characters).",
        "present_count": len(meaningful),
        "distinct_count": len(distinct),
        "average_length": average_length,
    }


def _detail_quality_capability_check(
    quality: dict[str, Any],
    *,
    detail_follow_enabled: bool,
) -> dict[str, Any] | None:
    if not detail_follow_enabled:
        return None
    status = str(quality.get("status") or "")
    return {
        "capability": "detail_description_quality",
        "label": "Detail description quality",
        "expected": True,
        "observed": status in {"good", "weak"},
        "status": "pass" if status == "good" else "warning" if status == "weak" else "observed",
        "detail": str(quality.get("summary") or ""),
    }


def _description_is_title_like(description: str, title: str) -> bool:
    description_norm = _quality_text(description)
    title_norm = _quality_text(title)
    if not description_norm or not title_norm:
        return False
    if description_norm == title_norm:
        return True
    if title_norm in description_norm and len(description_norm) <= len(title_norm) + 60:
        return True
    return description_norm in title_norm


def _quality_text(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value).lower()).split())


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
    path = runtime_jobs_dir(root) / "seen_jobs.json"
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
    explanations = listing_count_explanations(
        observed=_int(metadata.get("listing_observed_count")),
        retained=job_count,
        missing_url=_int(metadata.get("listing_missing_url_count")),
        rejected=_int(metadata.get("listing_rejected_count")),
        duplicates=_int(metadata.get("listing_duplicate_count")),
        limited=_int(metadata.get("listing_limit_skipped_count")),
    )
    visible_total = _int(metadata.get("visible_total_job_count"))
    if visible_total and visible_total > job_count:
        explanations.append(
            f"Visible total check: the source appears to advertise {visible_total} posting(s), "
            f"while this test retained {job_count}."
        )
    seen_note = seen_state_explanation(
        new=seen_counts.get("new", 0),
        changed=seen_counts.get("changed", 0),
        previously_seen=seen_counts.get("previously_seen", 0),
        job_count=job_count,
    )
    if seen_note:
        explanations.append(seen_note)
    return explanations


def _emit_progress(
    callback: SourceTestProgressCallback | None,
    phase: str,
    status: str,
    detail: str,
    capability: str = "",
) -> None:
    if callback:
        callback({"phase": phase, "status": status, "detail": detail, "capability": capability})


def _fetch_source_test_adapter(
    adapter,
    progress_callback: SourceTestProgressCallback | None,
    *,
    material_log: SourceTestMaterialLog | None = None,
):
    options = SourceFetchOptions(
        fetch_details=True,
        use_source_job_limit=False,
        use_recipe_card_limit=False,
        detail_page_limit=SOURCE_TEST_DETAIL_PAGE_LIMIT,
        detail_listing_page_sample_target=SOURCE_TEST_DETAIL_LISTING_PAGE_SAMPLE_TARGET,
        pagination_page_limit=0,
        material_log=material_log,
        access_purpose="source_test",
    )
    kwargs: dict[str, Any] = {}
    if _adapter_accepts_parameter(adapter, "progress_callback") and progress_callback:
        kwargs["progress_callback"] = progress_callback
    if _adapter_accepts_parameter(adapter, "options"):
        kwargs["options"] = options
    return adapter.fetch(**kwargs)


def _finalize_material_log(
    material_log: SourceTestMaterialLog | None,
    result: SourceTestResult,
    *,
    source_run_metadata: dict[str, Any] | None = None,
) -> None:
    if not material_log:
        return
    result.log_dir = material_log.relative_dir
    result.log_manifest_path = material_log.relative_manifest_path
    material_log.finalize(result, source_run_metadata=source_run_metadata)


def _adapter_accepts_parameter(adapter, name: str) -> bool:
    try:
        parameters = signature(adapter.fetch).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(parameter.kind == Parameter.VAR_KEYWORD or parameter.name == name for parameter in parameters)
