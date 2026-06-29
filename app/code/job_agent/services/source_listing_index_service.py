from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_agent.config import ROOT
from job_agent.models import Job
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.source_listing_index_store import SourceListingIndexStore
from job_agent.sources import SourceFetchOptions, adapter_for_source, saved_readiness_warning_for_source
from job_agent.store import JobStore

SourceListingIndexProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class SourceListingIndexResult:
    source_id: str
    source_name: str = ""
    source_type: str = ""
    status: str = "not_found"
    job_count: int = 0
    reviewed_in_detail_count: int = 0
    waiting_for_detail_count: int = 0
    no_longer_posted_count: int = 0
    page_explored_count: int = 0
    page_total: int = 0
    pagination_strategy: str = ""
    pagination_fetch_count: int = 0
    pagination_duplicate_page_count: int = 0
    pagination_duplicate_ratio: float = 0.0
    warning_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.status == "not_found":
            return "Source is not available for listing scans."
        if self.status == "failed":
            return f"Listing scan failed: {self.warnings[0]}" if self.warnings else "Listing scan failed."
        summary = (
            f"{self.job_count} jobs indexed, "
            f"{self.reviewed_in_detail_count} reviewed in detail, "
            f"{self.waiting_for_detail_count} waiting for detail review."
        )
        if self.no_longer_posted_count:
            summary += (
                f" {self.no_longer_posted_count} historical posting"
                f"{'' if self.no_longer_posted_count == 1 else 's'} no longer posted."
            )
        if self.pagination_duplicate_page_count:
            summary += (
                f" {self.pagination_duplicate_page_count} pagination page"
                f"{'' if self.pagination_duplicate_page_count == 1 else 's'} returned duplicate or inaccessible listings."
            )
        if self.warning_count:
            summary += f" {self.warning_count} warning{'' if self.warning_count == 1 else 's'}."
        return summary


class SourceListingIndexService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.execution_sources = ExecutionSourceService(root)

    def record_source_test_index(self, result: Any) -> SourceListingIndexResult:
        source_id = str(getattr(result, "source_id", "") or "").strip()
        if not source_id:
            return SourceListingIndexResult(source_id="", status="not_found")
        source = self.execution_sources.find_by_source_id(source_id)
        source_name = str(getattr(result, "source_name", "") or "")
        source_type = str(getattr(result, "source_type", "") or "")
        if source:
            source_name = source_name or str(source.get("name") or source_id)
            source_type = source_type or str(source.get("type") or "")
        else:
            source_name = source_name or source_id
        jobs = _jobs_from_source_test_result(result, source_id=source_id, source_name=source_name)
        if not jobs:
            return SourceListingIndexResult(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                status="failed",
                warning_count=1,
                warnings=["Source test returned no listings to index."],
            )

        job_store = JobStore(self.root, create=False)
        states = job_store.classify(jobs, identity_only=True)
        index_summary = SourceListingIndexStore(self.root).record_index(
            source_id=source_id,
            source_name=source_name,
            jobs=jobs,
        )
        reviewed = sum(1 for state in states if state.status == "previously_seen")
        warnings = [str(warning) for warning in getattr(result, "warnings", []) or []]
        pagination_fetch_count = _int(getattr(result, "pagination_fetch_count", 0))
        page_explored_count = max(1 if jobs else 0, 1 + pagination_fetch_count)
        page_total = max(page_explored_count, _int(getattr(result, "pagination_max_pages", 0)) or 0)
        return SourceListingIndexResult(
            source_id=source_id,
            source_name=source_name,
            source_type=source_type,
            status="completed_with_warnings" if warnings else "completed",
            job_count=index_summary.indexed_count,
            reviewed_in_detail_count=reviewed,
            waiting_for_detail_count=max(0, index_summary.indexed_count - reviewed),
            no_longer_posted_count=index_summary.no_longer_posted_count,
            page_explored_count=page_explored_count,
            page_total=page_total,
            pagination_strategy=str(getattr(result, "pagination_strategy", "") or ""),
            pagination_fetch_count=pagination_fetch_count,
            pagination_duplicate_page_count=_int(getattr(result, "pagination_duplicate_page_count", 0)),
            pagination_duplicate_ratio=float(getattr(result, "pagination_duplicate_ratio", 0.0) or 0.0),
            warning_count=len(warnings),
            warnings=warnings,
        )

    def index(
        self,
        source_id: str,
        progress_callback: SourceListingIndexProgressCallback | None = None,
    ) -> SourceListingIndexResult:
        source_id = source_id.strip()
        source = self.execution_sources.find_by_source_id(source_id)
        if not source:
            return SourceListingIndexResult(source_id=source_id)

        source_name = str(source.get("name") or source_id)
        source_type = str(source.get("type") or "")
        readiness_warning = saved_readiness_warning_for_source(source, self.root, enforce=True)
        if readiness_warning:
            return SourceListingIndexResult(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                status="failed",
                warning_count=1,
                warnings=[readiness_warning],
            )
        try:
            result = adapter_for_source(source, self.root).fetch(
                progress_callback=progress_callback,
                options=SourceFetchOptions(
                    fetch_details=False,
                    use_source_job_limit=False,
                    use_recipe_card_limit=False,
                    pagination_page_limit=0,
                    enforce_saved_readiness=True,
                    access_purpose="listing_index",
                ),
            )
        except Exception as exc:
            return SourceListingIndexResult(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                status="failed",
                warning_count=1,
                warnings=[str(exc)],
            )

        job_store = JobStore(self.root)
        states = job_store.classify(result.jobs, identity_only=True)
        index_summary = SourceListingIndexStore(self.root).record_index(
            source_id=source_id,
            source_name=source_name,
            jobs=result.jobs,
        )
        no_longer_posted_count = self._reconcile_seen_posting_status(
            job_store=job_store,
            source_id=source_id,
            source_name=source_name,
            source_url=str(source.get("url") or ""),
            current_listing_keys={JobStore.listing_key(job) for job in result.jobs},
        )
        no_longer_posted_count = max(no_longer_posted_count, index_summary.no_longer_posted_count)
        reviewed = sum(1 for state in states if state.status == "previously_seen")
        warnings = [f"{warning.source}: {warning.message}" for warning in result.warnings]
        metadata = result.metadata or {}
        pagination_fetch_count = _int(metadata.get("pagination_fetch_count"))
        page_explored_count = max(1 if result.jobs else 0, 1 + pagination_fetch_count)
        page_total = max(page_explored_count, _int(metadata.get("pagination_max_pages")) or 0)
        return SourceListingIndexResult(
            source_id=source_id,
            source_name=source_name,
            source_type=source_type,
            status="completed_with_warnings" if warnings else "completed",
            job_count=index_summary.indexed_count,
            reviewed_in_detail_count=reviewed,
            waiting_for_detail_count=max(0, index_summary.indexed_count - reviewed),
            no_longer_posted_count=no_longer_posted_count,
            page_explored_count=page_explored_count,
            page_total=page_total,
            pagination_strategy=str(metadata.get("pagination_strategy") or ""),
            pagination_fetch_count=pagination_fetch_count,
            pagination_duplicate_page_count=_int(metadata.get("pagination_duplicate_page_count")),
            pagination_duplicate_ratio=float(metadata.get("pagination_duplicate_ratio") or 0.0),
            warning_count=len(warnings),
            warnings=warnings,
        )

    def _reconcile_seen_posting_status(
        self,
        *,
        job_store: JobStore,
        source_id: str,
        source_name: str,
        source_url: str,
        current_listing_keys: set[str],
    ) -> int:
        seen_records = [
            record
            for record in job_store.list_seen_records()
            if record.listing_key and _seen_record_matches_source(record, source_id, source_name, source_url)
        ]
        active_keys = {record.listing_key for record in seen_records if record.listing_key in current_listing_keys}
        stale_keys = {record.listing_key for record in seen_records if record.listing_key not in current_listing_keys}
        job_store.update_posting_status(active_keys, "active")
        job_store.update_posting_status(stale_keys, "no_longer_posted")
        return len(stale_keys)


def _seen_record_matches_source(record, source_id: str, source_name: str, source_url: str) -> bool:
    record_source = str(record.source or "").strip().lower()
    source_names = {source_id.strip().lower(), source_name.strip().lower()}
    if record_source and record_source in source_names:
        return True
    return bool(source_url and record.url and _same_host_path(source_url, record.url))


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _jobs_from_source_test_result(result: Any, *, source_id: str, source_name: str) -> list[Job]:
    jobs = []
    for item in getattr(result, "jobs", []) or []:
        title = str(getattr(item, "title", "") or "").strip()
        if not title:
            continue
        description = str(getattr(item, "description", "") or getattr(item, "description_preview", "") or "")
        jobs.append(
            Job(
                title=title,
                source=str(getattr(item, "source", "") or source_name or source_id),
                source_id=str(getattr(item, "source_id", "") or source_id),
                url=str(getattr(item, "url", "") or ""),
                location=str(getattr(item, "location", "") or "Not listed"),
                remote=str(getattr(item, "remote", "") or "Not listed"),
                rate=str(getattr(item, "rate", "") or "Not listed"),
                workload=str(getattr(item, "workload", "") or "Not listed"),
                posted_date=str(getattr(item, "posted_date", "") or "Not listed"),
                start_date=str(getattr(item, "start_date", "") or "Not listed"),
                languages=list(getattr(item, "languages", []) or []),
                description=description,
                extraction_notes=list(getattr(item, "extraction_notes", []) or []),
            )
        )
    return jobs


def _same_host_path(left: str, right: str) -> bool:
    left_parsed = urlparse(left if "://" in left else f"https://{left}")
    right_parsed = urlparse(right if "://" in right else f"https://{right}")
    left_host = left_parsed.netloc.lower().removeprefix("www.")
    right_host = right_parsed.netloc.lower().removeprefix("www.")
    if not left_host or left_host != right_host:
        return False
    left_path = left_parsed.path.rstrip("/")
    right_path = right_parsed.path.rstrip("/")
    return not left_path or right_path == left_path or right_path.startswith(f"{left_path}/")
