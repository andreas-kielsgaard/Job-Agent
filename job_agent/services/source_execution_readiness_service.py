from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.source_registry_service import SourceRegistryService
from job_agent.services.source_session_service import SourceSessionService
from job_agent.services.source_test_service import SourceTestResult

READINESS_PATH = Path("sources/source-execution-readiness.yaml")


@dataclass
class SourceExecutionReadiness:
    source_id: str
    last_checked_at: str = ""
    dry_run_status: str = "untested"
    dry_run_job_count: int = 0
    dry_run_warning_count: int = 0
    dry_run_warnings: list[str] = field(default_factory=list)
    dry_run_capability_checks: list[dict[str, Any]] = field(default_factory=list)
    dry_run_pagination_duplicate_page_count: int = 0
    dry_run_pagination_duplicate_ratio: float = 0.0
    dry_run_pagination_unique_jobs_from_fetched_pages: int = 0
    dry_run_forced_disabled: bool = False
    source_type: str = ""
    execution_enabled_at_check: bool = False
    sample_titles: list[str] = field(default_factory=list)
    sample_urls: list[str] = field(default_factory=list)
    readiness_status: str = "untested"
    readiness_summary: str = "No source test readiness has been saved yet."
    checks: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class SourceEnablementCheck:
    source_id: str
    can_enable: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    readiness: SourceExecutionReadiness = field(default_factory=lambda: SourceExecutionReadiness(source_id=""))


@dataclass
class SourceEnablementResult:
    source_id: str
    enabled: bool
    check: SourceEnablementCheck


class SourceExecutionReadinessService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.path = self.root / READINESS_PATH
        self.registry = SourceRegistryService(self.root)
        self.execution = ExecutionSourceService(self.root)
        self.candidates = RecipeCandidateStore(self.root)

    def load(self, source_id: str) -> SourceExecutionReadiness:
        data = read_yaml(self.path, {"sources": {}})
        sources = data.get("sources", {}) if isinstance(data, dict) else {}
        record = sources.get(source_id) if isinstance(sources, dict) else None
        if not isinstance(record, dict):
            return SourceExecutionReadiness(source_id=source_id)
        return _record_from_mapping(source_id, record)

    def load_all(self) -> dict[str, SourceExecutionReadiness]:
        data = read_yaml(self.path, {"sources": {}})
        sources = data.get("sources", {}) if isinstance(data, dict) else {}
        if not isinstance(sources, dict):
            return {}
        return {
            str(source_id): _record_from_mapping(str(source_id), record)
            for source_id, record in sources.items()
            if isinstance(record, dict)
        }

    def save_from_source_test(self, result: SourceTestResult) -> SourceExecutionReadiness:
        readiness = self.evaluate(result.source_id, source_test_result=result)
        data = read_yaml(self.path, {"sources": {}})
        if not isinstance(data, dict):
            data = {"sources": {}}
        sources = data.setdefault("sources", {})
        if not isinstance(sources, dict):
            sources = {}
            data["sources"] = sources
        sources[result.source_id] = _record_to_mapping(readiness)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(self.path, data)
        return readiness

    def save_from_dry_run(self, result: SourceTestResult) -> SourceExecutionReadiness:
        return self.save_from_source_test(result)

    def evaluate(
        self,
        source_id: str,
        source_test_result: SourceTestResult | None = None,
    ) -> SourceExecutionReadiness:
        source = self.registry.get_source(source_id)
        execution_entry = self.execution.find_by_source_id(source_id)
        saved = self.load(source_id)
        checks, blockers, warnings = self._checks(source_id, source, execution_entry)
        session_required, session_status = _source_session_status(self.root, source)
        checks["source_session_required"] = session_required
        checks["source_session_status"] = session_status.status if session_status else ""
        checks["source_session_usable"] = bool(session_status and session_status.usable)
        checks["source_session_verified"] = bool(session_status and session_status.verified_at)
        checks["source_session_verified_at"] = session_status.verified_at if session_status else ""
        checks["source_session_scope"] = session_status.session_scope if session_status else ""
        if session_required and session_status and not session_status.usable:
            blockers.append(f"Connected source session is required; current session status is {session_status.label}.")
        if source_test_result:
            dry_status = source_test_result.status
            job_count = source_test_result.job_count
            warning_count = source_test_result.warning_count
            dry_warnings = list(source_test_result.warnings)
            capability_checks = list(source_test_result.capability_checks)
            pagination_duplicate_page_count = source_test_result.pagination_duplicate_page_count
            pagination_duplicate_ratio = source_test_result.pagination_duplicate_ratio
            pagination_unique_jobs_from_fetched_pages = source_test_result.pagination_unique_jobs_from_fetched_pages
            forced_disabled = source_test_result.forced_disabled
            source_type = source_test_result.source_type
            enabled_at_check = source_test_result.source_enabled
            sample_titles = [job.title for job in source_test_result.jobs[:5]]
            sample_urls = [job.url for job in source_test_result.jobs[:5] if job.url]
            last_checked_at = _now()
            _record_source_test_metrics(checks, source_test_result)
        else:
            dry_status = saved.dry_run_status
            job_count = saved.dry_run_job_count
            warning_count = saved.dry_run_warning_count
            dry_warnings = list(saved.dry_run_warnings)
            capability_checks = list(saved.dry_run_capability_checks)
            pagination_duplicate_page_count = saved.dry_run_pagination_duplicate_page_count
            pagination_duplicate_ratio = saved.dry_run_pagination_duplicate_ratio
            pagination_unique_jobs_from_fetched_pages = saved.dry_run_pagination_unique_jobs_from_fetched_pages
            forced_disabled = saved.dry_run_forced_disabled
            source_type = saved.source_type
            enabled_at_check = saved.execution_enabled_at_check
            sample_titles = list(saved.sample_titles)
            sample_urls = list(saved.sample_urls)
            last_checked_at = saved.last_checked_at
            _copy_saved_source_test_metrics(checks, saved.checks)

        recipe_changed_after_source_test = False
        if not source_test_result:
            recipe_changed_after_source_test = _recipe_changed_after_source_test(
                self.root,
                source.recipe_path if source else "",
                last_checked_at,
            )
            if recipe_changed_after_source_test:
                blockers.append("Reading plan changed since the saved source test; rerun the safe source test.")
            if session_required and session_status and session_status.usable and not session_status.verified_at:
                blockers.append("Source session is connected but not verified; verify the session with a source test.")
            if (
                session_required
                and session_status
                and session_status.usable
                and session_status.verified_at
                and _timestamp_after(session_status.connected_at, session_status.verified_at)
            ):
                blockers.append("Source session changed since it was verified; verify the session again.")
            if (
                session_required
                and session_status
                and session_status.usable
                and _timestamp_after(session_status.connected_at, last_checked_at)
            ):
                blockers.append("Source session changed since the saved source test; rerun the safe source test.")

        if recipe_changed_after_source_test:
            dry_blockers, dry_review_warnings = [], []
        else:
            dry_blockers, dry_review_warnings = _source_test_findings(
                dry_status,
                job_count,
                dry_warnings,
                capability_checks,
                pagination_duplicate_page_count,
                pagination_duplicate_ratio,
            )
        blockers.extend(dry_blockers)
        warnings.extend(dry_review_warnings)
        checks["source_test_capability_checks"] = capability_checks
        checks["pagination_duplicate_page_count"] = pagination_duplicate_page_count
        checks["pagination_duplicate_ratio"] = pagination_duplicate_ratio
        checks["pagination_unique_jobs_from_fetched_pages"] = pagination_unique_jobs_from_fetched_pages
        checks["recipe_changed_after_source_test"] = recipe_changed_after_source_test
        readiness_status = _derive_readiness_status(blockers, warnings, last_checked_at)
        return SourceExecutionReadiness(
            source_id=source_id,
            last_checked_at=last_checked_at,
            dry_run_status=dry_status,
            dry_run_job_count=job_count,
            dry_run_warning_count=warning_count,
            dry_run_warnings=dry_warnings,
            dry_run_capability_checks=capability_checks,
            dry_run_pagination_duplicate_page_count=pagination_duplicate_page_count,
            dry_run_pagination_duplicate_ratio=pagination_duplicate_ratio,
            dry_run_pagination_unique_jobs_from_fetched_pages=pagination_unique_jobs_from_fetched_pages,
            dry_run_forced_disabled=forced_disabled,
            source_type=source_type,
            execution_enabled_at_check=enabled_at_check,
            sample_titles=sample_titles,
            sample_urls=sample_urls,
            readiness_status=readiness_status,
            readiness_summary=_summary(readiness_status, job_count, blockers, warnings),
            checks=checks,
            blockers=blockers,
            warnings=warnings,
        )

    def can_enable(self, source_id: str) -> SourceEnablementCheck:
        readiness = self.evaluate(source_id)
        blockers = list(readiness.blockers)
        if readiness.readiness_status != "ready":
            blockers.append(
                f"Saved readiness is {readiness.readiness_status}; run and save a successful source test first."
            )
        execution_entry = self.execution.find_by_source_id(source_id)
        if execution_entry and bool(execution_entry.get("enabled", True)):
            blockers.append("Source is already included in daily runs.")
        return SourceEnablementCheck(
            source_id=source_id,
            can_enable=not blockers,
            blockers=blockers,
            warnings=list(readiness.warnings),
            readiness=readiness,
        )

    def enable_when_ready(self, source_id: str) -> SourceEnablementResult:
        check = self.can_enable(source_id)
        if not check.can_enable:
            return SourceEnablementResult(source_id=source_id, enabled=False, check=check)
        self.execution.enable(source_id)
        return SourceEnablementResult(source_id=source_id, enabled=True, check=check)

    def _checks(self, source_id: str, source, execution_entry) -> tuple[dict[str, Any], list[str], list[str]]:
        checks = {
            "source_exists": source is not None,
            "registry_recipe_path_present": bool(source and source.recipe_path),
            "source_health_status": source.health.health_status if source else "missing",
            "adopted_recipe_path_matches_registry": False,
            "execution_entry_exists": execution_entry is not None,
            "execution_entry_recipe_path_matches_registry": False,
            "execution_entry_enabled": bool(execution_entry and execution_entry.get("enabled", True)),
        }
        blockers = []
        warnings = []
        if not source:
            blockers.append("Source registry entry was not found.")
            return checks, blockers, warnings
        if not source.recipe_path:
            blockers.append("Source registry has no recipe_path.")
        adopted_path = _latest_adopted_path(self.candidates, source_id)
        checks["adopted_recipe_path_matches_registry"] = bool(
            adopted_path and _normalize_path(adopted_path) == _normalize_path(source.recipe_path)
        )
        if adopted_path and not checks["adopted_recipe_path_matches_registry"]:
            warnings.append("Latest adopted recipe path differs from the source registry recipe_path.")
        if not execution_entry:
            blockers.append("No daily-run projection exists.")
        else:
            execution_recipe_path = str(execution_entry.get("recipe_path") or "")
            checks["execution_entry_recipe_path_matches_registry"] = _normalize_path(
                execution_recipe_path
            ) == _normalize_path(source.recipe_path)
            if not checks["execution_entry_recipe_path_matches_registry"]:
                blockers.append("Daily-run projection recipe_path does not match source registry recipe_path.")
        return checks, blockers, warnings


_SOURCE_TEST_METRIC_KEYS = (
    "access_strategy",
    "api_request_count",
    "records_observed_count",
    "json_records_extracted_count",
    "pagination_strategy",
    "pagination_fetch_count",
    "pagination_link_count",
    "pagination_max_pages",
    "interactive_pagination_control_count",
    "visible_total_job_count",
    "listing_observed_count",
    "listing_extracted_count",
    "detail_fetch_count",
    "detail_enriched_count",
    "detail_fetch_limit",
    "detail_verified_listing_page_count",
)


def _record_source_test_metrics(checks: dict[str, Any], result: SourceTestResult) -> None:
    checks.update(
        {
            "pagination_strategy": result.pagination_strategy,
            "access_strategy": result.access_strategy,
            "api_request_count": result.api_request_count,
            "records_observed_count": result.records_observed_count,
            "json_records_extracted_count": result.json_records_extracted_count,
            "pagination_fetch_count": result.pagination_fetch_count,
            "pagination_link_count": result.pagination_link_count,
            "pagination_max_pages": result.pagination_max_pages,
            "interactive_pagination_control_count": result.interactive_pagination_control_count,
            "visible_total_job_count": result.visible_total_job_count,
            "listing_observed_count": result.listing_observed_count,
            "listing_extracted_count": result.listing_extracted_count,
            "detail_fetch_count": result.detail_fetch_count,
            "detail_enriched_count": result.detail_enriched_count,
            "detail_fetch_limit": result.detail_fetch_limit,
            "detail_verified_listing_page_count": result.detail_verified_listing_page_count,
        }
    )


def _copy_saved_source_test_metrics(checks: dict[str, Any], saved_checks: dict[str, Any]) -> None:
    for key in _SOURCE_TEST_METRIC_KEYS:
        if key in saved_checks:
            checks[key] = saved_checks[key]


def _source_test_findings(
    status: str,
    job_count: int,
    warnings: list[str],
    capability_checks: list[dict[str, Any]],
    pagination_duplicate_page_count: int,
    pagination_duplicate_ratio: float,
) -> tuple[list[str], list[str]]:
    blockers = []
    review_warnings = []
    if not status or status == "untested":
        blockers.append("No saved source test readiness result.")
    elif status in {"not_found", "disabled", "failing"}:
        blockers.append(f"Source test status is {status}.")
    if job_count <= 0:
        blockers.append("Source test extracted no jobs.")
    pagination_failures = [
        check
        for check in capability_checks
        if str(check.get("capability") or "")
        in {
            "pagination_navigation",
            "listing_total_access",
            "pagination_strategy",
            "ajax_pagination",
            "api_pagination",
            "browser_click_pagination",
            "pagination_duplicate_pages",
            "source_access",
        }
        and str(check.get("status") or "") == "fail"
    ]
    if pagination_failures:
        explicit_source_access_failure = next(
            (
                check
                for check in pagination_failures
                if str(check.get("capability") or "") == "source_access"
            ),
            None,
        )
        inferred_source_access_failure = next(
            (
                check
                for check in pagination_failures
                if _mentions_source_access(str(check.get("detail") or ""))
            ),
            None,
        )
        source_access_failure = explicit_source_access_failure or inferred_source_access_failure
        failure_priority = {
            "source_access": 0,
            "pagination_strategy": 1,
            "ajax_pagination": 2,
            "api_pagination": 2,
            "browser_click_pagination": 2,
            "pagination_navigation": 3,
            "pagination_duplicate_pages": 4,
            "listing_total_access": 5,
        }
        primary_failure = source_access_failure or min(
            pagination_failures,
            key=lambda check: failure_priority.get(str(check.get("capability") or ""), 99),
        )
        detail = str(primary_failure.get("detail") or "").strip()
        capability = str(primary_failure.get("capability") or "")
        failure_labels = {
            "source_access": "Source access verification failed",
            "listing_total_access": "Listing coverage verification failed",
            "pagination_strategy": "Pagination strategy verification failed",
        }
        label = (
            "Source access verification failed"
            if source_access_failure
            else failure_labels.get(capability, "Pagination verification failed")
        )
        blockers.append(label + (f": {detail}" if detail else "."))
    if warnings:
        review_warnings.append(f"Source test reported {len(warnings)} warnings.")
    return blockers, review_warnings


def _derive_readiness_status(blockers: list[str], warnings: list[str], last_checked_at: str) -> str:
    if not last_checked_at:
        return "untested"
    if blockers:
        return "blocked"
    if warnings:
        return "warning"
    return "ready"


def _mentions_source_access(value: str) -> bool:
    lowered = value.lower()
    return any(
        token in lowered
        for token in ["logged-in", "login", "sign-in", "session", "auth", "registration gate"]
    )


def _summary(status: str, job_count: int, blockers: list[str], warnings: list[str]) -> str:
    if status == "ready":
        return f"Ready: source test extracted {job_count} jobs and readiness checks passed."
    if status == "warning":
        return f"Warning: source test extracted {job_count} jobs with review warnings."
    if status == "blocked":
        return f"Blocked: {blockers[0] if blockers else 'readiness checks did not pass'}"
    return "No source test readiness has been saved yet."


def _latest_adopted_path(store: RecipeCandidateStore, source_id: str) -> str:
    candidates = []
    for summary in store.list_candidates(status="approved"):
        try:
            candidate = store.load_candidate(summary.candidate_id)
        except ValueError:
            continue
        if candidate.adopted_source_id == source_id and candidate.adopted_recipe_path:
            candidates.append(candidate)
    if not candidates:
        return ""
    return sorted(candidates, key=lambda item: item.adopted_at, reverse=True)[0].adopted_recipe_path


def _record_to_mapping(record: SourceExecutionReadiness) -> dict[str, Any]:
    return {
        "last_checked_at": record.last_checked_at,
        "dry_run_status": record.dry_run_status,
        "dry_run_job_count": record.dry_run_job_count,
        "dry_run_warning_count": record.dry_run_warning_count,
        "dry_run_warnings": record.dry_run_warnings,
        "dry_run_capability_checks": record.dry_run_capability_checks,
        "dry_run_pagination_duplicate_page_count": record.dry_run_pagination_duplicate_page_count,
        "dry_run_pagination_duplicate_ratio": record.dry_run_pagination_duplicate_ratio,
        "dry_run_pagination_unique_jobs_from_fetched_pages": record.dry_run_pagination_unique_jobs_from_fetched_pages,
        "dry_run_forced_disabled": record.dry_run_forced_disabled,
        "source_type": record.source_type,
        "execution_enabled_at_check": record.execution_enabled_at_check,
        "sample_titles": record.sample_titles,
        "sample_urls": record.sample_urls,
        "readiness_status": record.readiness_status,
        "readiness_summary": record.readiness_summary,
        "checks": record.checks,
        "blockers": record.blockers,
        "warnings": record.warnings,
    }


def _record_from_mapping(source_id: str, data: dict[str, Any]) -> SourceExecutionReadiness:
    return SourceExecutionReadiness(
        source_id=source_id,
        last_checked_at=str(data.get("last_checked_at") or ""),
        dry_run_status=str(data.get("dry_run_status") or "untested"),
        dry_run_job_count=_int(data.get("dry_run_job_count")),
        dry_run_warning_count=_int(data.get("dry_run_warning_count")),
        dry_run_warnings=_list(data.get("dry_run_warnings")),
        dry_run_capability_checks=_dict_list(data.get("dry_run_capability_checks")),
        dry_run_pagination_duplicate_page_count=_int(data.get("dry_run_pagination_duplicate_page_count")),
        dry_run_pagination_duplicate_ratio=_float(data.get("dry_run_pagination_duplicate_ratio")),
        dry_run_pagination_unique_jobs_from_fetched_pages=_int(
            data.get("dry_run_pagination_unique_jobs_from_fetched_pages")
        ),
        dry_run_forced_disabled=bool(data.get("dry_run_forced_disabled", False)),
        source_type=str(data.get("source_type") or ""),
        execution_enabled_at_check=bool(data.get("execution_enabled_at_check", False)),
        sample_titles=_list(data.get("sample_titles")),
        sample_urls=_list(data.get("sample_urls")),
        readiness_status=str(data.get("readiness_status") or "untested"),
        readiness_summary=str(data.get("readiness_summary") or "No source test readiness has been saved yet."),
        checks=data.get("checks") if isinstance(data.get("checks"), dict) else {},
        blockers=_list(data.get("blockers")),
        warnings=_list(data.get("warnings")),
    )


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value:
        return [str(value)]
    return []


def _dict_list(value) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _recipe_changed_after_source_test(root: Path, recipe_path: str, last_checked_at: str) -> bool:
    if not recipe_path or not last_checked_at:
        return False
    path = root / recipe_path
    if not path.exists():
        return False
    try:
        checked_at = datetime.fromisoformat(last_checked_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return modified_at > checked_at


def _source_session_status(root: Path, source) -> tuple[bool, Any | None]:
    if not source or not getattr(source, "recipe_path", ""):
        return False, None
    try:
        from job_agent.services.recipes.mapping import load_job_board_recipe

        recipe = load_job_board_recipe(Path(root) / source.recipe_path)
    except (OSError, ValueError):
        return False, None
    if not recipe.access.requires_session:
        return False, None
    status = SourceSessionService(root).status_for_source(
        source.id,
        session_scope=recipe.access.session_scope,
    )
    return True, status


def _timestamp_after(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        left_at = datetime.fromisoformat(left.replace("Z", "+00:00"))
        right_at = datetime.fromisoformat(right.replace("Z", "+00:00"))
    except ValueError:
        return False
    if left_at.tzinfo is None:
        left_at = left_at.replace(tzinfo=UTC)
    if right_at.tzinfo is None:
        right_at = right_at.replace(tzinfo=UTC)
    return left_at > right_at


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
