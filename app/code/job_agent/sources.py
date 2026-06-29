from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from inspect import Parameter, signature
from pathlib import Path
from queue import Queue
from threading import Lock
from time import perf_counter, sleep
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

from .config import ROOT
from .models import Job, SourceRunResult, SourceWarning
from .paths import resolve_project_path


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
    details: dict[str, Any] = field(default_factory=dict)


SourceProgressCallback = Callable[[SourceProgressEvent], None]
SourceFetchProgressCallback = Callable[[dict[str, Any]], None]

_HOST_LOCKS: dict[str, Lock] = {}
_HOST_LOCKS_GUARD = Lock()
_WAITABLE_SOURCE_ACCESS_STATUSES = {"needs_login", "needs_verification"}


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
    require_setup_complete: bool = False
    max_parallel_sources: int | None = 10
    material_log: Any | None = None
    access_purpose: str = "daily_run"
    wait_for_source_access: bool = False
    source_access_wait_timeout_seconds: float = 0.0
    source_access_wait_poll_seconds: float = 2.0


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
        path = resolve_project_path(self.root, self.source["path"])
        _emit_fetch_progress(progress_callback, "Local YAML read", "running", f"Reading {path}.")
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        options = options or SourceFetchOptions()
        _record_material_text(
            options.material_log,
            "input/local-source.yaml",
            path.read_text(encoding="utf-8"),
            kind="local_yaml",
            metadata={"source_path": _relative_path(path, self.root)},
        )
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
        _record_material_html(
            options.material_log,
            kind="generic_listing",
            url=url,
            final_url=str(response.url),
            html=response.text,
            mode="static_html",
        )
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
            from .services.recipes.mapping import load_project_job_board_recipe
            from .services.source_access_gate_service import SourceAccessGateService

            recipe = load_project_job_board_recipe(self.root, recipe_path)
            options = options or SourceFetchOptions()
            _record_material_recipe(options.material_log, recipe_path)
            _emit_fetch_progress(
                progress_callback,
                "Recipe selected",
                "completed",
                f"Using {recipe.source_name} from {recipe_path}.",
            )
            access_decision = SourceAccessGateService(self.root).evaluate_source(
                self.source,
                purpose=options.access_purpose,
            )
            if not access_decision.can_execute:
                _emit_fetch_progress(
                    progress_callback,
                    "Source access blocked",
                    "failed",
                    access_decision.message,
                    "source_access",
                )
                return SourceRunResult(
                    warnings=[SourceWarning(source_name, access_decision.message, url)],
                    metadata=_source_access_block_metadata(
                        source=self.source,
                        recipe_path=recipe_path,
                        recipe=recipe,
                        decision=access_decision,
                    ),
                )
            session_state_path = options.session_state_path or access_decision.session_state_path
            if session_state_path and access_decision.uses_session:
                _emit_fetch_progress(
                    progress_callback,
                    "Source session selected",
                    "completed",
                    access_decision.message,
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
                session_state_path=resolve_project_path(self.root, session_state_path) if session_state_path else None,
                material_log=options.material_log,
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
                access_decision=access_decision,
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
    if fetch_options and fetch_options.require_setup_complete and not source_id:
        sources, skipped_sources = _filter_setup_complete_sources(sources, root, fetch_options)
        if skipped_sources:
            _emit_source_progress(
                progress_callback,
                SourceProgressEvent(
                    event_type="source_setup_skipped",
                    source_name="Daily-run setup",
                    source_index=0,
                    source_count=len(sources),
                    warnings_count=len(skipped_sources),
                    message=_setup_skipped_message(skipped_sources),
                    details={
                        "skipped_source_count": len(skipped_sources),
                        "skipped_sources": [
                            _setup_skipped_source_detail(source, reason, root) for source, reason in skipped_sources
                        ],
                    },
                ),
            )
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
    submitted = 0
    max_workers = _source_worker_limit(len(items), fetch_options)
    pending_items = iter(items)

    def worker(item: _SourceWorkItem) -> None:
        result = _run_source_item(
            item,
            root,
            lambda event: queue.put(_SourceQueueItem("event", event=event)),
            fetch_options,
        )
        queue.put(_SourceQueueItem("result", result=result))

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="source-fetch") as executor:

        def submit_next() -> bool:
            nonlocal submitted
            try:
                item = next(pending_items)
            except StopIteration:
                return False
            executor.submit(worker, item)
            submitted += 1
            return True

        for _ in range(max_workers):
            submit_next()
        while completed < submitted:
            item = queue.get()
            if item.kind == "event" and item.event:
                _emit_source_progress(progress_callback, item.event)
            elif item.kind == "result" and item.result:
                completed += 1
                submit_next()
                yield item.result


def _source_worker_limit(source_count: int, fetch_options: SourceFetchOptions | None = None) -> int:
    if source_count <= 0:
        return 0
    configured = fetch_options.max_parallel_sources if fetch_options else 10
    try:
        limit = int(configured or 10)
    except (TypeError, ValueError):
        limit = 10
    return max(1, min(source_count, limit))


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
        access_decision, access_warning = _source_access_check_for_source(
            item.source,
            root,
            enforce=bool(fetch_options and fetch_options.enforce_saved_readiness),
            purpose=(fetch_options.access_purpose if fetch_options else "daily_run"),
        )
        if access_warning and _should_wait_for_source_access(access_decision, fetch_options):
            access_decision, access_warning = _wait_for_source_access(
                item,
                root,
                progress_callback,
                fetch_options,
                access_decision,
                started_at=started_at,
            )
        if access_warning:
            source_result = SourceRunResult(warnings=[SourceWarning(item.source_name, access_warning, item.source_url)])
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
            details=_source_progress_metadata_counts(source_result.metadata, len(source_result.jobs)),
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


def _source_progress_metadata_counts(metadata: dict[str, Any], job_count: int) -> dict[str, Any]:
    if not metadata:
        return {}
    details = {
        "listing_observed_count": _int(metadata.get("listing_observed_count")),
        "listing_extracted_count": _int(metadata.get("listing_extracted_count")),
        "listing_limit_skipped_count": _int(metadata.get("listing_limit_skipped_count")),
        "pagination_fetch_count": _int(metadata.get("pagination_fetch_count")),
        "pagination_duplicate_page_count": _int(metadata.get("pagination_duplicate_page_count")),
        "pagination_max_pages": _int(metadata.get("pagination_max_pages")),
        "visible_total_job_count": _int(metadata.get("visible_total_job_count")),
        "detail_fetch_count": _int(metadata.get("detail_fetch_count")),
        "detail_enriched_count": _int(metadata.get("detail_enriched_count")),
    }
    pagination_fetch_count = int(details["pagination_fetch_count"] or 0)
    page_explored_count = max(1 if metadata or job_count else 0, 1 + pagination_fetch_count)
    page_total = max(page_explored_count, int(details["pagination_max_pages"] or 0))
    visible_total = int(details["visible_total_job_count"] or 0)
    observed = int(details["listing_extracted_count"] or job_count or 0)
    if visible_total and observed and page_explored_count:
        per_page = max(1, round(observed / page_explored_count))
        page_total = max(page_total, (visible_total + per_page - 1) // per_page)
    details["page_explored_count"] = page_explored_count
    details["page_total"] = page_total
    return {key: value for key, value in details.items() if value not in {0, "", None}}


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


def _record_material_html(material_log: Any, **kwargs: Any) -> None:
    if not material_log:
        return
    recorder = getattr(material_log, "record_html", None)
    if callable(recorder):
        recorder(**kwargs)


def _record_material_text(
    material_log: Any,
    filename: str,
    content: str,
    *,
    kind: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not material_log:
        return
    recorder = getattr(material_log, "record_text", None)
    if callable(recorder):
        recorder(filename, content, kind=kind, metadata=metadata)


def _record_material_recipe(material_log: Any, recipe_path: str) -> None:
    if not material_log:
        return
    recorder = getattr(material_log, "record_recipe", None)
    if callable(recorder):
        recorder(recipe_path)


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def saved_readiness_warning_for_source(
    source: dict,
    root: Path,
    *,
    enforce: bool = False,
) -> str:
    return source_access_warning_for_source(source, root, enforce=enforce, purpose="daily_run")


def source_access_decision_for_source(
    source: dict,
    root: Path,
    *,
    enforce: bool = False,
    purpose: str = "daily_run",
) -> Any | None:
    decision, _warning = _source_access_check_for_source(source, root, enforce=enforce, purpose=purpose)
    return decision


def source_access_warning_for_source(
    source: dict,
    root: Path,
    *,
    enforce: bool = False,
    purpose: str = "daily_run",
) -> str:
    _decision, warning = _source_access_check_for_source(source, root, enforce=enforce, purpose=purpose)
    return warning


def _source_access_check_for_source(
    source: dict,
    root: Path,
    *,
    enforce: bool = False,
    purpose: str = "daily_run",
) -> tuple[Any | None, str]:
    if not enforce:
        return None, ""
    source_id = str(source.get("source_id") or "").strip()
    if not source_id or not str(source.get("recipe_path") or "").strip():
        return None, ""
    try:
        from .services.source_access_gate_service import SourceAccessGateService

        decision = SourceAccessGateService(root).evaluate_source(source, purpose=purpose, source_id=source_id)
    except Exception as exc:
        return None, f"Saved source readiness could not be checked before execution: {exc}"
    return decision, "" if decision.can_execute else decision.message


def _should_wait_for_source_access(
    decision: Any | None,
    fetch_options: SourceFetchOptions | None,
) -> bool:
    if not fetch_options or not fetch_options.wait_for_source_access:
        return False
    if decision is None:
        return False
    return str(getattr(decision, "status", "") or "") in _WAITABLE_SOURCE_ACCESS_STATUSES


def _wait_for_source_access(
    item: _SourceWorkItem,
    root: Path,
    progress_callback: SourceProgressCallback | None,
    fetch_options: SourceFetchOptions | None,
    initial_decision: Any,
    *,
    started_at: float,
) -> tuple[Any | None, str]:
    timeout_seconds = _source_access_wait_timeout_seconds(fetch_options)
    poll_seconds = _source_access_wait_poll_seconds(fetch_options)
    purpose = fetch_options.access_purpose if fetch_options else "daily_run"
    deadline = perf_counter() + timeout_seconds
    decision = initial_decision
    _emit_source_access_event(
        progress_callback,
        "source_access_waiting",
        item,
        decision,
        message=f"Waiting for source access for {item.source_name}: {decision.message}",
        started_at=started_at,
        wait_timeout_seconds=timeout_seconds,
    )
    last_status = str(getattr(decision, "status", "") or "")
    while True:
        remaining_seconds = deadline - perf_counter()
        if remaining_seconds <= 0:
            decision, warning = _source_access_check_for_source(
                item.source,
                root,
                enforce=True,
                purpose=purpose,
            )
            if decision is not None and decision.can_execute:
                _emit_source_access_event(
                    progress_callback,
                    "source_access_resumed",
                    item,
                    decision,
                    message=f"Source access refreshed for {item.source_name}; resuming execution.",
                    started_at=started_at,
                )
                return decision, ""
            _emit_source_access_event(
                progress_callback,
                "source_access_timeout",
                item,
                decision,
                message=f"Source access still blocked for {item.source_name}: {warning}",
                started_at=started_at,
                warnings_count=1,
            )
            return decision, warning
        sleep(min(poll_seconds, remaining_seconds))
        decision, warning = _source_access_check_for_source(item.source, root, enforce=True, purpose=purpose)
        if decision is not None and decision.can_execute:
            _emit_source_access_event(
                progress_callback,
                "source_access_resumed",
                item,
                decision,
                message=f"Source access refreshed for {item.source_name}; resuming execution.",
                started_at=started_at,
            )
            return decision, ""
        current_status = str(getattr(decision, "status", "") or "")
        if current_status and current_status != last_status:
            _emit_source_access_event(
                progress_callback,
                "source_access_waiting",
                item,
                decision,
                message=f"Waiting for source access for {item.source_name}: {warning}",
                started_at=started_at,
                wait_timeout_seconds=timeout_seconds,
            )
            last_status = current_status
        if decision is None or current_status not in _WAITABLE_SOURCE_ACCESS_STATUSES:
            _emit_source_access_event(
                progress_callback,
                "source_access_timeout",
                item,
                decision,
                message=f"Source access can no longer wait for {item.source_name}: {warning}",
                started_at=started_at,
                warnings_count=1,
            )
            return decision, warning


def _source_access_wait_timeout_seconds(fetch_options: SourceFetchOptions | None) -> float:
    if not fetch_options:
        return 0.0
    try:
        return max(0.0, float(fetch_options.source_access_wait_timeout_seconds or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _source_access_wait_poll_seconds(fetch_options: SourceFetchOptions | None) -> float:
    if not fetch_options:
        return 2.0
    try:
        return max(0.05, float(fetch_options.source_access_wait_poll_seconds or 2.0))
    except (TypeError, ValueError):
        return 2.0


def _emit_source_access_event(
    callback: SourceProgressCallback | None,
    event_type: str,
    item: _SourceWorkItem,
    decision: Any | None,
    *,
    message: str,
    started_at: float,
    warnings_count: int = 0,
    wait_timeout_seconds: float | None = None,
) -> None:
    details = _source_access_progress_details(item.source, decision)
    if wait_timeout_seconds is not None:
        details["source_access_wait_timeout_seconds"] = wait_timeout_seconds
    _emit_source_progress(
        callback,
        SourceProgressEvent(
            event_type=event_type,
            source_name=item.source_name,
            source_index=item.source_index,
            source_count=item.source_count,
            source_type=item.source_type,
            source_url=item.source_url,
            warnings_count=warnings_count,
            elapsed_time_seconds=round(perf_counter() - started_at, 3),
            message=message,
            details=details,
        ),
    )


def _source_access_progress_details(source: dict, decision: Any | None) -> dict[str, Any]:
    source_id = str(source.get("source_id") or "").strip()
    status = str(getattr(decision, "status", "") or "").strip()
    message = str(getattr(decision, "message", "") or "").strip()
    action_href = f"/sources/{source_id}" if source_id else ""
    action_label = "Open source"
    if status in {"needs_login", "needs_verification"} and source_id:
        action_href = f"/sources/{source_id}/session"
        action_label = "Verify session" if status == "needs_verification" else "Connect session"
    elif status == "blocked" and source_id:
        action_href = f"/sources/{source_id}/test-run?start=1"
        action_label = "Run source test"
    return {
        "source_id": source_id,
        "source_access_status": status,
        "source_access_message": message,
        "source_action_href": action_href,
        "source_action_label": action_label,
    }


def _filter_setup_complete_sources(
    sources: list[dict],
    root: Path,
    fetch_options: SourceFetchOptions | None = None,
) -> tuple[list[dict], list[tuple[dict, str]]]:
    included = []
    skipped = []
    for source in sources:
        ready, reason = _source_setup_complete_for_daily_run(
            source,
            root,
            wait_for_source_access=bool(fetch_options and fetch_options.wait_for_source_access),
        )
        if ready:
            included.append(source)
        else:
            skipped.append((source, reason))
    return included, skipped


def _source_setup_complete_for_daily_run(
    source: dict,
    root: Path,
    *,
    wait_for_source_access: bool = False,
) -> tuple[bool, str]:
    if source.get("type") != "recipe_html" or not str(source.get("recipe_path") or "").strip():
        return True, ""
    source_id = str(source.get("source_id") or "").strip()
    if not source_id:
        return False, "missing source_id"
    try:
        from .services.source_access_gate_service import SourceAccessGateService

        access_decision = SourceAccessGateService(root).evaluate_source(
            source, purpose="daily_run", source_id=source_id
        )
    except Exception as exc:
        return False, f"source access check failed: {exc}"
    if not access_decision.can_execute:
        if wait_for_source_access and str(access_decision.status) in _WAITABLE_SOURCE_ACCESS_STATUSES:
            setup_reason = _daily_run_saved_setup_blocker(source, root, source_id)
            return (False, setup_reason) if setup_reason else (True, "")
        return False, access_decision.message
    setup_reason = _daily_run_saved_setup_blocker(source, root, source_id)
    return (False, setup_reason) if setup_reason else (True, "")


def _daily_run_saved_setup_blocker(source: dict, root: Path, source_id: str) -> str:
    try:
        from .services.source_execution_readiness_service import SourceExecutionReadinessService

        service = SourceExecutionReadinessService(root)
        readiness = service.load(source_id)
        registry_source = service.registry.get_source(source_id)
        if registry_source:
            readiness = service.with_current_recipe_file_checks(registry_source, readiness)
    except Exception as exc:
        return f"readiness check failed: {exc}"
    if readiness.readiness_status != "ready":
        return f"readiness is {readiness.readiness_status}"
    try:
        from .services.source_listing_index_store import SourceListingIndexStore

        index = SourceListingIndexStore(root).summary_for_source(source_id, str(source.get("name") or source_id))
    except Exception as exc:
        return f"listing index check failed: {exc}"
    if not index.is_indexed:
        return "listing index is missing"
    return ""


def _setup_skipped_message(skipped_sources: list[tuple[dict, str]]) -> str:
    names = []
    for source, reason in skipped_sources[:5]:
        name = str(source.get("name") or source.get("source_id") or "Unknown source")
        names.append(f"{name} ({reason})" if reason else name)
    suffix = "" if len(skipped_sources) <= 5 else f" and {len(skipped_sources) - 5} more"
    return "Skipped sources still in setup: " + ", ".join(names) + suffix + "."


def _setup_skipped_source_detail(source: dict, reason: str, root: Path) -> dict[str, Any]:
    source_id = str(source.get("source_id") or "").strip()
    detail: dict[str, Any] = {
        "source_name": str(source.get("name") or source_id or "Unknown source"),
        "source_id": source_id,
        "reason": reason,
        "source_access_status": "",
        "source_action_href": f"/sources/{source_id}" if source_id else "",
        "source_action_label": "Open source",
    }
    if not source_id or not str(source.get("recipe_path") or "").strip():
        return detail
    try:
        from .services.source_access_gate_service import SourceAccessGateService

        decision = SourceAccessGateService(root).evaluate_source(source, purpose="daily_run", source_id=source_id)
    except Exception:
        return detail
    detail["source_access_status"] = decision.status
    if decision.status in {"needs_login", "needs_verification"}:
        detail["source_action_href"] = f"/sources/{source_id}/session"
        detail["source_action_label"] = (
            "Verify session" if decision.status == "needs_verification" else "Connect session"
        )
    elif decision.status == "blocked" and str((decision.metadata or {}).get("readiness_status") or "").strip():
        detail["source_action_href"] = f"/sources/{source_id}/test-run?start=1"
        detail["source_action_label"] = "Run source test"
    return detail


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
    access_decision: Any | None = None,
) -> dict[str, Any]:
    api_pagination_configured = bool(
        getattr(recipe, "listing_api", None)
        and recipe.listing_api.url
        and recipe.listing_api.pagination.strategy != "none"
    )
    html_pagination_configured = bool(
        recipe.pagination.strategy in {"ajax", "browser_click"}
        or recipe.pagination.page_link_selector
        or recipe.pagination.next_selector
    )
    pagination_strategy = result.pagination_strategy_used or (
        f"api_{recipe.listing_api.pagination.strategy}" if api_pagination_configured else recipe.pagination.strategy
    )
    pagination_max_pages = (
        recipe.listing_api.pagination.max_pages if api_pagination_configured else recipe.pagination.max_pages
    )
    access_metadata = dict(getattr(access_decision, "metadata", {}) or {})
    metadata = {
        "adapter": "recipe_html",
        "recipe_path": recipe_path,
        "recipe_source_name": recipe.source_name,
        "base_url": result.base_url,
        "mode_used": result.mode_used,
        "access_strategy": getattr(result, "access_strategy", "html"),
        "api_request_count": getattr(result, "api_request_count", 0),
        "records_observed_count": getattr(result, "records_observed_count", 0),
        "json_records_extracted_count": getattr(result, "json_records_extracted_count", 0),
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
        "pagination_configured": bool(api_pagination_configured or html_pagination_configured),
        "pagination_strategy": pagination_strategy,
        "pagination_ajax_url_template_present": bool(recipe.pagination.ajax_url_template),
        "pagination_click_selector_configured": bool(
            recipe.pagination.click_selector or recipe.pagination.next_selector or recipe.pagination.page_link_selector
        ),
        "pagination_link_count": len(result.pagination_links),
        "pagination_max_pages": pagination_max_pages,
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
        "source_access_session_status": "",
        "source_access_session_label": "",
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
    metadata.update(access_metadata)
    metadata["source_access_session_used"] = bool(result.source_access_session_used) or bool(
        metadata.get("source_access_session_used")
    )
    return metadata


def _source_access_block_metadata(
    *,
    source: dict,
    recipe_path: str,
    recipe: Any,
    decision: Any,
) -> dict[str, Any]:
    detail = decision.message
    access_metadata = dict(getattr(decision, "metadata", {}) or {})
    return {
        **access_metadata,
        "adapter": "recipe_html",
        "recipe_path": recipe_path,
        "recipe_source_name": recipe.source_name,
        "base_url": source.get("url", ""),
        "configured_source_url": source.get("url", ""),
        "source_access_requires_session": bool(getattr(decision, "session_required", False)),
        "source_access_session_used": False,
        "source_access_session_scope": recipe.access.session_scope,
        "source_access_setup_hint": recipe.access.setup_hint,
        "source_access_session_status": access_metadata.get("source_access_session_status", ""),
        "source_access_session_label": access_metadata.get("source_access_session_label", ""),
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
