from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.source_dry_run_service import SourceDryRunResult
from job_agent.services.source_registry_service import SourceRegistryService

READINESS_PATH = Path("sources/source-execution-readiness.yaml")


@dataclass
class SourceExecutionReadiness:
    source_id: str
    last_checked_at: str = ""
    dry_run_status: str = "untested"
    dry_run_job_count: int = 0
    dry_run_warning_count: int = 0
    dry_run_warnings: list[str] = field(default_factory=list)
    dry_run_forced_disabled: bool = False
    source_type: str = ""
    execution_enabled_at_check: bool = False
    sample_titles: list[str] = field(default_factory=list)
    sample_urls: list[str] = field(default_factory=list)
    readiness_status: str = "untested"
    readiness_summary: str = "No source dry-run readiness has been saved yet."
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

    def save_from_dry_run(self, result: SourceDryRunResult) -> SourceExecutionReadiness:
        readiness = self.evaluate(result.source_id, dry_run_result=result)
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

    def evaluate(
        self,
        source_id: str,
        dry_run_result: SourceDryRunResult | None = None,
    ) -> SourceExecutionReadiness:
        source = self.registry.get_source(source_id)
        execution_entry = self.execution.find_by_source_id(source_id)
        saved = self.load(source_id)
        checks, blockers, warnings = self._checks(source_id, source, execution_entry)
        if dry_run_result:
            dry_status = dry_run_result.status
            job_count = dry_run_result.job_count
            warning_count = dry_run_result.warning_count
            dry_warnings = list(dry_run_result.warnings)
            forced_disabled = dry_run_result.forced_disabled
            source_type = dry_run_result.source_type
            enabled_at_check = dry_run_result.source_enabled
            sample_titles = [job.title for job in dry_run_result.jobs[:5]]
            sample_urls = [job.url for job in dry_run_result.jobs[:5] if job.url]
            last_checked_at = _now()
        else:
            dry_status = saved.dry_run_status
            job_count = saved.dry_run_job_count
            warning_count = saved.dry_run_warning_count
            dry_warnings = list(saved.dry_run_warnings)
            forced_disabled = saved.dry_run_forced_disabled
            source_type = saved.source_type
            enabled_at_check = saved.execution_enabled_at_check
            sample_titles = list(saved.sample_titles)
            sample_urls = list(saved.sample_urls)
            last_checked_at = saved.last_checked_at

        dry_blockers, dry_review_warnings = _dry_run_findings(dry_status, job_count, dry_warnings)
        blockers.extend(dry_blockers)
        warnings.extend(dry_review_warnings)
        readiness_status = _derive_readiness_status(blockers, warnings, last_checked_at)
        return SourceExecutionReadiness(
            source_id=source_id,
            last_checked_at=last_checked_at,
            dry_run_status=dry_status,
            dry_run_job_count=job_count,
            dry_run_warning_count=warning_count,
            dry_run_warnings=dry_warnings,
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
            blockers.append(f"Saved readiness is {readiness.readiness_status}; run and save a successful dry-run first.")
        execution_entry = self.execution.find_by_source_id(source_id)
        if execution_entry and bool(execution_entry.get("enabled", True)):
            blockers.append("Execution entry is already enabled.")
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
        if source.health.health_status != "good":
            blockers.append(f"Source health must be good before enablement; current status is {source.health.health_status}.")
        adopted_path = _latest_adopted_path(self.candidates, source_id)
        checks["adopted_recipe_path_matches_registry"] = bool(
            adopted_path and _normalize_path(adopted_path) == _normalize_path(source.recipe_path)
        )
        if adopted_path and not checks["adopted_recipe_path_matches_registry"]:
            warnings.append("Latest adopted recipe path differs from the source registry recipe_path.")
        if not execution_entry:
            blockers.append("No daily-run execution entry exists.")
        else:
            execution_recipe_path = str(execution_entry.get("recipe_path") or "")
            checks["execution_entry_recipe_path_matches_registry"] = (
                _normalize_path(execution_recipe_path) == _normalize_path(source.recipe_path)
            )
            if not checks["execution_entry_recipe_path_matches_registry"]:
                blockers.append("Execution entry recipe_path does not match source registry recipe_path.")
        return checks, blockers, warnings


def _dry_run_findings(status: str, job_count: int, warnings: list[str]) -> tuple[list[str], list[str]]:
    blockers = []
    review_warnings = []
    if not status or status == "untested":
        blockers.append("No saved dry-run readiness result.")
    elif status in {"not_found", "disabled", "failing"}:
        blockers.append(f"Dry-run status is {status}.")
    if job_count <= 0:
        blockers.append("Dry-run extracted no jobs.")
    if warnings:
        review_warnings.append(f"Dry-run reported {len(warnings)} warnings.")
    return blockers, review_warnings


def _derive_readiness_status(blockers: list[str], warnings: list[str], last_checked_at: str) -> str:
    if not last_checked_at:
        return "untested"
    if blockers:
        return "blocked"
    if warnings:
        return "warning"
    return "ready"


def _summary(status: str, job_count: int, blockers: list[str], warnings: list[str]) -> str:
    if status == "ready":
        return f"Ready: dry-run extracted {job_count} jobs and readiness checks passed."
    if status == "warning":
        return f"Warning: dry-run extracted {job_count} jobs with review warnings."
    if status == "blocked":
        return f"Blocked: {blockers[0] if blockers else 'readiness checks did not pass'}"
    return "No source dry-run readiness has been saved yet."


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
        dry_run_forced_disabled=bool(data.get("dry_run_forced_disabled", False)),
        source_type=str(data.get("source_type") or ""),
        execution_enabled_at_check=bool(data.get("execution_enabled_at_check", False)),
        sample_titles=_list(data.get("sample_titles")),
        sample_urls=_list(data.get("sample_urls")),
        readiness_status=str(data.get("readiness_status") or "untested"),
        readiness_summary=str(data.get("readiness_summary") or "No source dry-run readiness has been saved yet."),
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


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
