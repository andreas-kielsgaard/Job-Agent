from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from inspect import Parameter, signature
from pathlib import Path
from queue import Queue
from threading import Lock
from time import perf_counter
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

from .config import ROOT
from .models import Job, SourceRunResult, SourceWarning


@dataclass
class SourceProgressEvent:
    event_type: str
    source_name: str
    source_index: int
    source_count: int
    message: str
    source_type: str = ""
    source_url: str = ""
    jobs_found: int = 0
    warnings_count: int = 0
    elapsed_time_seconds: float | None = None
    page_explored_count: int = 0
    page_total: int = 0


SourceProgressCallback = Callable[[SourceProgressEvent], None]
SourceFetchProgressCallback = Callable[[dict[str, Any]], None]

_HOST_LOCKS: dict[str, Lock] = {}
_HOST_LOCKS_GUARD = Lock()


@dataclass
class SourceFetchOptions:
    fetch_details: bool = True
    use_source_job_limit: bool = True
    use_recipe_card_limit: bool = True
    detail_page_limit: int | None = None
    detail_success_target: int | None = None
    detail_listing_page_sample_target: int | None = None
    pagination_page_limit: int | None = None
    session_state_path: str | Path | None = None
    enforce_saved_readiness: bool = False


@dataclass
class SourceFetchResult:
    source: dict
    source_name: str
    source_index: int
    source_count: int
    result: SourceRunResult
    elapsed_time_seconds: float | None = None


@dataclass
class _SourceWorkItem:
    source: dict
    source_name: str
    source_index: int
    source_count: int
    source_type: str
    source_url: str


@dataclass
class _SourceQueueItem:
    kind: str
    event: SourceProgressEvent | None = None
    result: SourceFetchResult | None = None


class SourceAdapter(ABC):
    def __init__(self, source: dict, root: Path = ROOT) -> None:
        self.source = source
        self.root = root

    @abstractmethod
    def fetch(
        self,
        progress_callback: SourceFetchProgressCallback | None = None,
        options: SourceFetchOptions | None = None,
    ) -> SourceRunResult:
        raise NotImplementedError


class LocalYamlAdapter(SourceAdapter):
    def fetch(
        self,
        progress_callback: SourceFetchProgressCallback | None = None,
        options: SourceFetchOptions | None = None,
    ) -> SourceRunResult:
        path = self.root / self.source["path"]
        _emit_fetch_progress(progress_callback, "Local YAML read", "running", f"Reading {path}.")
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        jobs = []
        today = str(date.today())
        for item in data.get("jobs", []):
            item.setdefault("source", self.source.get("name", "Local YAML"))
            item.setdefault("source_id", self.source.get("source_id", ""))
            item.setdefault("first_seen_date", today)
            item.setdefault("source_confidence", "high")
            item.setdefault("freshness_confidence", "explicit" if item.get("posted_date") else "unknown")
            jobs.append(Job.from_mapping(item))
        _emit_fetch_progress(progress_callback, "Local YAML parsed", "completed", f"Loaded {len(jobs)} job(s).")
        return SourceRunResult(jobs=jobs)


class GenericHtmlAdapter(SourceAdapter):
    """Best-effort public HTML extractor.

    This adapter is intentionally conservative. If it cannot find plausible listing
    links, it returns a source warning instead of manufacturing a fake job from the
    whole page.
    """

    def fetch(
        self,
        progress_callback: SourceFetchProgressCallback | None = None,
        options: SourceFetchOptions | None = None,
    ) -> SourceRunResult:
        url = self.source.get("url", "")
        source_name = self.source.get("name", "Generic HTML")
        if not url:
            return SourceRunResult(warnings=[SourceWarning(source_name, "Source has no URL.")])

        try:
            _emit_fetch_progress(progress_callback, "Listing page request", "running", f"Fetching {url}.", "listing")
            response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
        except requests.RequestException as exc:
            return SourceRunResult(warnings=[SourceWarning(source_name, f"Fetch failed: {exc}", url)])
        _emit_fetch_progress(
            progress_callback, "Listing page fetched", "completed", f"Fetched {response.url}.", "listing"
        )

        options = options or SourceFetchOptions()
        max_results = _positive_int(self.source.get("max_results"), 25) if options.use_source_job_limit else None
        all_jobs = extract_generic_jobs_from_html(
            response.text,
            base_url=url,
            source_name=source_name,
            source_id=self.source.get("source_id", ""),
            max_results=None,
        )
        jobs = all_jobs[:max_results] if max_results is not None else all_jobs
        listing_limit_skipped_count = max(0, len(all_jobs) - len(jobs))
        _emit_fetch_progress(
            progress_callback,
            "Generic extraction",
            "completed",
            f"Found {len(jobs)} plausible job link(s).",
        )
        metadata = {
            "adapter": "generic_html",
            "listing_observed_count": len(all_jobs),
            "listing_extracted_count": len(jobs),
            "listing_limit_skipped_count": listing_limit_skipped_count,
            "job_limit": max_results,
        }
        if not jobs:
            return SourceRunResult(
                metadata=metadata,
                warnings=[
                    SourceWarning(
                        source_name,
                        "Generic HTML adapter found no plausible job links. Add a site-specific adapter or selectors.",
                        url,
                    )
                ],
            )
        return SourceRunResult(jobs=jobs, metadata=metadata)


class WhitehallResourcesAdapter(GenericHtmlAdapter):
    """Initial site-specific hook.

    The implementation currently uses generic link extraction, but keeping the
    adapter separate gives this source a natural place for selectors once tested.
    """


class RecipeHtmlAdapter(SourceAdapter):
    """Opt-in recipe-backed source adapter for configured daily-run sources."""

    def fetch(
        self,
        progress_callback: SourceFetchProgressCallback | None = None,
        options: SourceFetchOptions | None = None,
    ) -> SourceRunResult:
        source_name = self.source.get("name", "Recipe HTML")
        url = self.source.get("url", "")
        recipe_path = self.source.get("recipe_path", "")
        if not url:
            return SourceRunResult(warnings=[SourceWarning(source_name, "Recipe source has no URL.")])
        if not recipe_path:
            return SourceRunResult(warnings=[SourceWarning(source_name, "Recipe source has no recipe_path.", url)])

        try:
            from .services.job_board_recipe_service import extract_jobs_with_recipe_from_url
            from .services.recipes.mapping import load_job_board_recipe
            from .services.source_session_service import SourceSessionService

            recipe = load_job_board_recipe(self.root / recipe_path)
            options = options or SourceFetchOptions()
            _emit_fetch_progress(
                progress_callback,
                "Recipe selected",
                "completed",
                f"Using {recipe.source_name} from {recipe_path}.",
            )
            session_state_path = options.session_state_path
            session_status = None
            if not session_state_path:
                candidate_session_status = SourceSessionService(self.root).status_for_source(
                    str(self.source.get("source_id") or ""),
                    session_scope=recipe.access.session_scope,
                )
                if recipe.access.requires_session or candidate_session_status.usable:
                    session_status = candidate_session_status
            if recipe.access.requires_session and not session_state_path:
                if not session_status.usable:
                    message = (
                        f"Connected source session required for {recipe.access.session_scope or recipe.source_name}; "
                        f"current status is {session_status.label}. {session_status.summary}"
                    )
                    _emit_fetch_progress(
                        progress_callback, "Source session required", "failed", message, "source_access"
                    )
                    return SourceRunResult(
                        warnings=[SourceWarning(source_name, message, url)],
                        metadata=_session_required_metadata(
                            source=self.source,
                            recipe_path=recipe_path,
                            recipe=recipe,
                            session_status=session_status,
                        ),
                    )
                session_state_path = session_status.storage_state_path
            elif session_status and session_status.usable and not session_state_path:
                session_state_path = session_status.storage_state_path
            if session_state_path and session_status and session_status.usable:
                _emit_fetch_progress(
                    progress_callback,
                    "Source session selected",
                    "completed",
                    f"Using connected session for {session_status.session_scope or recipe.source_name}.",
                    "source_access",
                )
            max_results = (
                _optional_positive_int(self.source.get("max_results")) if options.use_source_job_limit else None
            )
            result = extract_jobs_with_recipe_from_url(
                url,
                recipe,
                timeout_seconds=30
                if recipe.mode == "rendered_html" or recipe.pagination.strategy == "browser_click"
                else 15,
                use_recipe_detail_limit=False,
                detail_page_limit=options.detail_page_limit,
                detail_success_target=options.detail_success_target,
                detail_listing_page_sample_target=options.detail_listing_page_sample_target,
                fetch_pagination=True,
                pagination_page_limit=options.pagination_page_limit,
                job_limit=max_results,
                fetch_details=options.fetch_details,
                use_recipe_card_limit=options.use_recipe_card_limit,
                session_state_path=self.root / session_state_path if session_state_path else None,
                progress_callback=lambda step: _emit_fetch_progress(
                    progress_callback,
                    step.phase,
                    step.status,
                    step.detail,
                    step.capability,
                    page_explored_count=step.page_explored_count,
                    page_total=step.page_total,
                    jobs_found=step.jobs_found,
                ),
            )
        except (OSError, ValueError) as exc:
            return SourceRunResult(warnings=[SourceWarning(source_name, f"Recipe extraction failed: {exc}", url)])

        jobs = result.jobs if max_results is None else result.jobs[:max_results]
        for job in jobs:
            job.source = source_name
            job.source_id = str(self.source.get("source_id") or "").strip()
            if not job.url:
                job.url = result.base_url
            if not job.application_url:
                job.application_url = job.url
        warnings = [SourceWarning(source_name, warning, result.base_url) for warning in result.warnings]
        return SourceRunResult(
            jobs=jobs,
            warnings=warnings,
            metadata=_recipe_result_metadata(
                source=self.source,
                recipe_path=recipe_path,
                recipe=recipe,
                result=result,
                retained_job_count=len(jobs),
                max_results=max_results,
                session_status=session_status,
            ),
        )


def load_sources(root: Path = ROOT) -> list[dict]:
    from .services.execution_source_service import ExecutionSourceService

    return [source for source in ExecutionSourceService(root).list_sources() if source.get("enabled", True)]


def _load_all_sources(root: Path = ROOT) -> list[dict]:
    from .services.execution_source_service import ExecutionSourceService

    return ExecutionSourceService(root).list_sources()


def iter_source_results(
    root: Path = ROOT,
    progress_callback: SourceProgressCallback | None = None,
    source_id: str = "",
    include_disabled: bool = False,
    fetch_options: SourceFetchOptions | None = None,
):
    sources = load_sources(root) if not include_disabled else _load_all_sources(root)
    if source_id:
        sources = [source for source in sources if str(source.get("source_id") or "") == source_id]
    source_count = len(sources)
    items = [
        _SourceWorkItem(
            source=source,
            source_name=source.get("name", "Unknown"),
            source_index=source_index,
            source_count=source_count,
            source_type=source.get("type", ""),
            source_url=source.get("url") or source.get("path", ""),
        )
        for source_index, source in enumerate(sources, start=1)
    ]
    if not items:
        return
    if len(items) == 1:
        yield _run_source_item(items[0], root, progress_callback, fetch_options)
        return

    yield from _iter_source_results_parallel(items, root, progress_callback, fetch_options)


def _iter_source_results_parallel(
    items: list[_SourceWorkItem],
    root: Path,
    progress_callback: SourceProgressCallback | None,
    fetch_options: SourceFetchOptions | None,
):
    queue: Queue[_SourceQueueItem] = Queue()
    completed = 0
    max_workers = min(3, len(items))

    def worker(item: _SourceWorkItem) -> None:
        result = _run_source_item(
            item,
            root,
            lambda event: queue.put(_SourceQueueItem("event", event=event)),
            fetch_options,
        )
        queue.put(_SourceQueueItem("result", result=result))

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="source-fetch") as executor:
        for item in items:
            executor.submit(worker, item)
        while completed < len(items):
            item = queue.get()
            if item.kind == "event" and item.event:
                _emit_source_progress(progress_callback, item.event)
            elif item.kind == "result" and item.result:
                completed += 1
                yield item.result


def _run_source_item(
    item: _SourceWorkItem,
    root: Path,
    progress_callback: SourceProgressCallback | None,
    fetch_options: SourceFetchOptions | None = None,
) -> SourceFetchResult:
    started_at = perf_counter()
    _emit_source_progress(
        progress_callback,
        SourceProgressEvent(
            event_type="source_started",
            source_name=item.source_name,
            source_index=item.source_index,
            source_count=item.source_count,
            source_type=item.source_type,
            source_url=item.source_url,
            message=f"Checking source {item.source_index}/{item.source_count}: {item.source_name}",
        ),
    )
    adapter = adapter_for_source(item.source, root)
    try:
        readiness_warning = saved_readiness_warning_for_source(
            item.source,
            root,
            enforce=bool(fetch_options and fetch_options.enforce_saved_readiness),
        )
        if readiness_warning:
            source_result = SourceRunResult(
                warnings=[SourceWarning(item.source_name, readiness_warning, item.source_url)]
            )
        else:
            host_lock = _host_lock(item.source_url)
            with host_lock:
                source_result = _fetch_adapter(
                    adapter,
                    lambda progress: _emit_adapter_progress(progress_callback, item, progress),
                    fetch_options,
                )
    except Exception as exc:
        elapsed = round(perf_counter() - started_at, 3)
        warning = SourceWarning(item.source_name, f"Source failed unexpectedly: {exc}", item.source_url)
        source_result = SourceRunResult(warnings=[warning])
        _emit_source_progress(
            progress_callback,
            SourceProgressEvent(
                event_type="source_failed",
                source_name=item.source_name,
                source_index=item.source_index,
                source_count=item.source_count,
                source_type=item.source_type,
                source_url=item.source_url,
                warnings_count=1,
                elapsed_time_seconds=elapsed,
                message=f"Source failed: {item.source_name} - {exc}",
            ),
        )
        return SourceFetchResult(
            source=item.source,
            source_name=item.source_name,
            source_index=item.source_index,
            source_count=item.source_count,
            result=source_result,
            elapsed_time_seconds=elapsed,
        )
    elapsed = round(perf_counter() - started_at, 3)
    for warning in source_result.warnings:
        _emit_source_progress(
            progress_callback,
            SourceProgressEvent(
                event_type="source_warning",
                source_name=warning.source,
                source_index=item.source_index,
                source_count=item.source_count,
                source_type=item.source_type,
                source_url=warning.url or item.source_url,
                warnings_count=1,
                elapsed_time_seconds=elapsed,
                message=f"Source warning from {warning.source}: {warning.message}",
            ),
        )
    _emit_source_progress(
        progress_callback,
        SourceProgressEvent(
            event_type="source_completed",
            source_name=item.source_name,
            source_index=item.source_index,
            source_count=item.source_count,
            source_type=item.source_type,
            source_url=item.source_url,
            jobs_found=len(source_result.jobs),
            warnings_count=len(source_result.warnings),
            elapsed_time_seconds=elapsed,
            message=(
                f"Completed source {item.source_index}/{item.source_count}: {item.source_name} - "
                f"{len(source_result.jobs)} jobs found, {len(source_result.warnings)} warnings"
            ),
        ),
    )
    return SourceFetchResult(
        source=item.source,
        source_name=item.source_name,
        source_index=item.source_index,
        source_count=item.source_count,
        result=source_result,
        elapsed_time_seconds=elapsed,
    )


def _emit_adapter_progress(
    callback: SourceProgressCallback | None,
    item: _SourceWorkItem,
    progress: dict[str, Any],
) -> None:
    phase = str(progress.get("phase") or "Source activity")
    detail = str(progress.get("detail") or "")
    _emit_source_progress(
        callback,
        SourceProgressEvent(
            event_type="source_activity",
            source_name=item.source_name,
            source_index=item.source_index,
            source_count=item.source_count,
            source_type=item.source_type,
            source_url=item.source_url,
            message=f"{phase}: {detail}" if detail else phase,
            jobs_found=_int(progress.get("jobs_found")),
            page_explored_count=_int(progress.get("page_explored_count")),
            page_total=_int(progress.get("page_total")),
        ),
    )


def _host_lock(url: str) -> Lock:
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    if not host:
        return Lock()
    with _HOST_LOCKS_GUARD:
        if host not in _HOST_LOCKS:
            _HOST_LOCKS[host] = Lock()
        return _HOST_LOCKS[host]


def _fetch_adapter(
    adapter: SourceAdapter,
    progress_callback: SourceFetchProgressCallback,
    fetch_options: SourceFetchOptions | None,
) -> SourceRunResult:
    kwargs: dict[str, Any] = {}
    if _adapter_accepts_parameter(adapter, "progress_callback"):
        kwargs["progress_callback"] = progress_callback
    if _adapter_accepts_parameter(adapter, "options"):
        kwargs["options"] = fetch_options
    return adapter.fetch(**kwargs)


def saved_readiness_warning_for_source(
    source: dict,
    root: Path,
    *,
    enforce: bool = False,
) -> str:
    if not enforce:
        return ""
    source_id = str(source.get("source_id") or "").strip()
    if not source_id or not str(source.get("recipe_path") or "").strip():
        return ""
    try:
        from .services.source_execution_readiness_service import SourceExecutionReadinessService

        service = SourceExecutionReadinessService(root)
        saved = service.load(source_id)
        if not saved.last_checked_at:
            return ""
        readiness = service.evaluate(source_id)
    except Exception as exc:
        return f"Saved source readiness could not be checked before execution: {exc}"
    if readiness.readiness_status == "ready":
        return ""
    detail = readiness.blockers[0] if readiness.blockers else readiness.readiness_summary
    return (
        f"Saved source readiness is {readiness.readiness_status}; {detail} "
        "Rerun the safe source test or refresh source access before this source is used."
    )


def _adapter_accepts_parameter(adapter: SourceAdapter, name: str) -> bool:
    try:
        parameters = signature(adapter.fetch).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(parameter.kind == Parameter.VAR_KEYWORD or parameter.name == name for parameter in parameters)


def _emit_source_progress(callback: SourceProgressCallback | None, event: SourceProgressEvent) -> None:
    if callback:
        callback(event)


def adapter_for_source(source: dict, root: Path = ROOT) -> SourceAdapter:
    source_type = source.get("type")
    name = source.get("name", "").lower()
    if source_type == "local_yaml":
        return LocalYamlAdapter(source, root)
    if source_type == "recipe_html":
        return RecipeHtmlAdapter(source, root)
    if "whitehall" in name:
        return WhitehallResourcesAdapter(source, root)
    if source_type in {"search_page", "generic_html"}:
        return GenericHtmlAdapter(source, root)
    return UnsupportedSourceAdapter(source, root)


def _recipe_result_metadata(
    *,
    source: dict,
    recipe_path: str,
    recipe: Any,
    result: Any,
    retained_job_count: int,
    max_results: int | None,
    session_status: Any | None = None,
) -> dict[str, Any]:
    return {
        "adapter": "recipe_html",
        "recipe_path": recipe_path,
        "recipe_source_name": recipe.source_name,
        "base_url": result.base_url,
        "mode_used": result.mode_used,
        "configured_source_url": source.get("url", ""),
        "retained_job_count": retained_job_count,
        "job_limit": max_results,
        "run_steps": [
            {
                "phase": step.phase,
                "status": step.status,
                "detail": step.detail,
                "capability": step.capability,
            }
            for step in result.steps
        ],
        "pagination_configured": bool(
            recipe.pagination.strategy in {"ajax", "browser_click"}
            or recipe.pagination.page_link_selector
            or recipe.pagination.next_selector
        ),
        "pagination_strategy": recipe.pagination.strategy,
        "pagination_ajax_url_template_present": bool(recipe.pagination.ajax_url_template),
        "pagination_click_selector_configured": bool(
            recipe.pagination.click_selector or recipe.pagination.next_selector or recipe.pagination.page_link_selector
        ),
        "pagination_link_count": len(result.pagination_links),
        "pagination_max_pages": recipe.pagination.max_pages,
        "pagination_fetch_count": result.pagination_fetch_count,
        "pagination_fetch_attempts": list(result.pagination_fetch_attempts),
        "pagination_duplicate_page_count": result.pagination_duplicate_page_count,
        "pagination_duplicate_ratio": result.pagination_duplicate_ratio,
        "pagination_unique_jobs_from_fetched_pages": result.pagination_unique_jobs_from_fetched_pages,
        "interactive_pagination_control_count": result.interactive_pagination_control_count,
        "source_access_requires_session": bool(recipe.access.requires_session),
        "source_access_session_used": bool(result.source_access_session_used),
        "source_access_session_scope": recipe.access.session_scope,
        "source_access_setup_hint": recipe.access.setup_hint,
        "source_access_session_status": getattr(session_status, "status", ""),
        "source_access_session_label": getattr(session_status, "label", ""),
        "source_access_login_gate_detected": bool(getattr(result, "source_access_login_gate_detected", False)),
        "listing_observed_count": result.listing_observed_count,
        "listing_extracted_count": result.listing_extracted_count,
        "listing_missing_url_count": result.listing_missing_url_count,
        "listing_rejected_count": result.listing_rejected_count,
        "listing_duplicate_count": result.listing_duplicate_count,
        "listing_limit_skipped_count": (
            result.listing_limit_skipped_count + max(0, len(result.jobs) - retained_job_count)
        ),
        "visible_total_job_count": result.visible_total_job_count,
        "listing_pages": [
            {
                "page_url": page.page_url,
                "observed_cards": page.observed_cards,
                "extracted_jobs": page.extracted_jobs,
                "missing_url_count": page.missing_url_count,
                "rejected_count": page.rejected_count,
                "duplicate_count": page.duplicate_count,
                "limit_skipped_count": page.limit_skipped_count,
                "limit": page.limit,
            }
            for page in result.listing_pages
        ],
        "detail_follow_enabled": recipe.detail.follow,
        "detail_fetch_limit": result.detail_fetch_limit,
        "detail_fetch_count": result.detail_fetch_count,
        "detail_enriched_count": result.detail_enriched_count,
        "detail_listing_page_sample_target": result.detail_listing_page_sample_target,
        "detail_verified_listing_page_count": result.detail_verified_listing_page_count,
        "detail_request_delay_seconds": recipe.detail.request_delay_seconds,
        "detail_attempts": [
            {
                "url": attempt.url,
                "status": attempt.status,
                "found_fields": list(attempt.found_fields),
                "missing_fields": list(attempt.missing_fields),
                "detail": attempt.detail,
            }
            for attempt in result.detail_attempts
        ],
        "field_checks": [
            {
                "field": check.field,
                "label": check.label,
                "scope": check.scope,
                "expected": check.expected,
                "status": check.status,
                "present_count": check.present_count,
                "total_count": check.total_count,
                "sample_value": check.sample_value,
                "source": check.source,
                "detail": check.detail,
            }
            for check in result.field_checks
        ],
        "capability_checks": [
            {
                "capability": check.capability,
                "label": check.label,
                "expected": check.expected,
                "observed": check.observed,
                "status": check.status,
                "detail": check.detail,
            }
            for check in result.capability_checks
        ],
    }


def _session_required_metadata(
    *,
    source: dict,
    recipe_path: str,
    recipe: Any,
    session_status: Any,
) -> dict[str, Any]:
    detail = (
        f"Recipe declares that this source requires a connected session. "
        f"Current session status: {session_status.label}. {session_status.summary}"
    )
    return {
        "adapter": "recipe_html",
        "recipe_path": recipe_path,
        "recipe_source_name": recipe.source_name,
        "base_url": source.get("url", ""),
        "configured_source_url": source.get("url", ""),
        "source_access_requires_session": True,
        "source_access_session_used": False,
        "source_access_session_scope": recipe.access.session_scope,
        "source_access_setup_hint": recipe.access.setup_hint,
        "source_access_session_status": session_status.status,
        "source_access_session_label": session_status.label,
        "capability_checks": [
            {
                "capability": "source_access",
                "label": "Source access",
                "expected": True,
                "observed": False,
                "status": "fail",
                "detail": detail,
            }
        ],
    }


class UnsupportedSourceAdapter(SourceAdapter):
    def fetch(
        self,
        progress_callback: SourceFetchProgressCallback | None = None,
        options: SourceFetchOptions | None = None,
    ) -> SourceRunResult:
        return SourceRunResult(
            warnings=[
                SourceWarning(
                    self.source.get("name", "Unknown"),
                    f"Unsupported source type: {self.source.get('type', 'missing')}.",
                    self.source.get("url", ""),
                )
            ]
        )


def _emit_fetch_progress(
    callback: SourceFetchProgressCallback | None,
    phase: str,
    status: str,
    detail: str,
    capability: str = "",
    *,
    page_explored_count: int = 0,
    page_total: int = 0,
    jobs_found: int = 0,
) -> None:
    if callback:
        callback(
            {
                "phase": phase,
                "status": status,
                "detail": detail,
                "capability": capability,
                "page_explored_count": page_explored_count,
                "page_total": page_total,
                "jobs_found": jobs_found,
            }
        )


JOB_HINTS = ("job", "career", "vacancy", "contract", "sap", "abap", "consultant")


def extract_generic_jobs_from_html(
    html: str,
    base_url: str,
    source_name: str,
    source_id: str = "",
    max_results: int | None = 25,
) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    for link in soup.find_all("a", href=True):
        title = link.get_text(" ", strip=True)
        href = urljoin(base_url, link["href"])
        haystack = f"{title} {href}".lower()
        if len(title) < 8 or not any(hint in haystack for hint in JOB_HINTS):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)
        surrounding = link.find_parent(["article", "li", "div", "section"])
        raw_text = surrounding.get_text("\n", strip=True) if surrounding else title
        jobs.append(
            Job(
                title=title,
                source=source_name,
                source_id=source_id,
                url=href,
                application_url=href,
                description=raw_text[:3000],
                raw_text=raw_text[:5000],
                source_confidence="medium",
                freshness_confidence="unknown",
                extraction_notes=["Generic HTML link extraction; verify details manually."],
            )
        )
    return jobs if max_results is None else jobs[:max_results]


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _optional_positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
