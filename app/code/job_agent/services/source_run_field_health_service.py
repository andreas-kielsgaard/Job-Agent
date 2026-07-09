from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.io.json_store import read_json
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.models import Job
from job_agent.paths import resolve_project_path, sources_dir
from job_agent.run_store import RunRecord, RunStore, utc_now
from job_agent.services.package_index_service import PackageIndexService
from job_agent.services.recipes.checks import expected_report_field_sources
from job_agent.services.recipes.mapping import load_project_job_board_recipe
from job_agent.services.source_registry_service import SourceRegistryService

RUN_FIELD_HEALTH_PATH = Path("sources/source-run-field-health.yaml")
SOURCE_RUN_FIELD_HEALTH_STATUSES = {"unknown", "not_applicable", "healthy", "warning", "needs_relearn"}
CORE_REQUIRED_FIELDS = {"title", "url"}
MIN_DESCRIPTION_CHARS = 80


@dataclass
class SourceFieldCoverage:
    field: str
    present_count: int
    total_count: int
    source: str = ""
    sample_value: str = ""

    @property
    def missing_count(self) -> int:
        return max(0, self.total_count - self.present_count)


@dataclass
class SourceRunFieldHealthRecord:
    source_id: str
    source_name: str = ""
    checked_at: str = ""
    run_id: str = ""
    recipe_path: str = ""
    job_count: int = 0
    expected_fields: list[str] = field(default_factory=list)
    field_coverage: dict[str, SourceFieldCoverage] = field(default_factory=dict)
    required_missing_fields: list[str] = field(default_factory=list)
    advisory_missing_fields: list[str] = field(default_factory=list)
    description_present_count: int = 0
    description_strong_count: int = 0
    headline_only_description_count: int = 0
    average_description_length: int = 0
    status: str = "unknown"
    summary: str = "No latest-run field health has been checked for this source yet."
    recommended_action: str = ""
    action_href: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def needs_action(self) -> bool:
        return self.status == "needs_relearn"


@dataclass
class SourceRunFieldHealthRefreshResult:
    run_id: str = ""
    run_started_at: str = ""
    status: str = "no_run"
    message: str = "No completed daily run is available to check."
    records: list[SourceRunFieldHealthRecord] = field(default_factory=list)
    skipped_package_count: int = 0

    @property
    def checked_source_count(self) -> int:
        return len(self.records)

    @property
    def needs_action_count(self) -> int:
        return sum(1 for record in self.records if record.needs_action)


class SourceRunFieldHealthService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.path = sources_dir(self.root) / "source-run-field-health.yaml"
        self.registry = SourceRegistryService(self.root)

    def load_all(self) -> dict[str, SourceRunFieldHealthRecord]:
        data = read_yaml(self.path, {"sources": {}})
        sources = data.get("sources", {}) if isinstance(data, dict) else {}
        if not isinstance(sources, dict):
            return {}
        return {
            str(source_id): _record_from_mapping(str(source_id), value)
            for source_id, value in sources.items()
            if isinstance(value, dict)
        }

    def get(self, source_id: str) -> SourceRunFieldHealthRecord:
        return self.load_all().get(source_id, SourceRunFieldHealthRecord(source_id=source_id))

    def refresh_from_latest_packages(self, source_id: str) -> SourceRunFieldHealthRecord:
        source = self.registry.get_source(source_id)
        if not source:
            return SourceRunFieldHealthRecord(
                source_id=source_id,
                status="unknown",
                summary="Source no longer exists in the registry.",
            )
        packages = [
            package
            for package in PackageIndexService(self.root).list_packages()
            if str(package.get("source_id") or "") == source_id
        ]
        if not packages:
            return self.get(source_id)
        run_id = _latest_run_id(packages)
        latest_packages = [package for package in packages if str(package.get("run_id") or "") == run_id]
        if not latest_packages:
            latest_packages = packages
        jobs = [_job for package in latest_packages if (_job := _job_from_package(self.root, package)) is not None]
        return self.update_from_jobs(
            source_id,
            source_name=source.name,
            jobs=jobs,
            run_id=run_id,
            recipe_path=source.recipe_path,
        )

    def refresh_latest_daily_run(self) -> SourceRunFieldHealthRefreshResult:
        run = _latest_completed_daily_run(RunStore(self.root))
        if run is None:
            return SourceRunFieldHealthRefreshResult()
        return self.refresh_run(run.run_id, run_started_at=run.started_at)

    def refresh_run(self, run_id: str, *, run_started_at: str = "") -> SourceRunFieldHealthRefreshResult:
        packages = PackageIndexService(self.root).list_packages(run_id)
        if not packages:
            return SourceRunFieldHealthRefreshResult(
                run_id=run_id,
                run_started_at=run_started_at,
                status="no_packages",
                message=f"Run {run_id} has no saved job packages to check.",
            )
        jobs_by_source: dict[str, list[Job]] = {}
        names_by_source: dict[str, str] = {}
        skipped_package_count = 0
        for package in packages:
            source_id = str(package.get("source_id") or "").strip()
            if not source_id:
                skipped_package_count += 1
                continue
            job = _job_from_package(self.root, package)
            if job is None:
                skipped_package_count += 1
                continue
            jobs_by_source.setdefault(source_id, []).append(job)
            names_by_source.setdefault(source_id, str(job.source or package.get("source") or source_id))
        records = [
            self.update_from_jobs(
                source_id,
                source_name=names_by_source.get(source_id, source_id),
                jobs=jobs,
                run_id=run_id,
            )
            for source_id, jobs in sorted(jobs_by_source.items())
        ]
        records = [record for record in records if record.status != "not_applicable"]
        if not records:
            return SourceRunFieldHealthRefreshResult(
                run_id=run_id,
                run_started_at=run_started_at,
                status="no_applicable_sources",
                message=f"Run {run_id} did not include recipe-backed sources with saved jobs.",
                skipped_package_count=skipped_package_count,
            )
        needs_action = sum(1 for record in records if record.needs_action)
        return SourceRunFieldHealthRefreshResult(
            run_id=run_id,
            run_started_at=run_started_at,
            status="needs_action" if needs_action else "checked",
            message=_refresh_summary(run_id, records, skipped_package_count),
            records=records,
            skipped_package_count=skipped_package_count,
        )

    def update_from_jobs(
        self,
        source_id: str,
        *,
        source_name: str = "",
        jobs: list[Job],
        run_id: str = "",
        recipe_path: str = "",
    ) -> SourceRunFieldHealthRecord:
        source = self.registry.get_source(source_id)
        source_name = source_name or (source.name if source else source_id)
        recipe_path = recipe_path or (source.recipe_path if source else "")
        record = evaluate_source_run_field_health(
            source_id=source_id,
            source_name=source_name,
            jobs=jobs,
            run_id=run_id,
            recipe_path=recipe_path,
            root=self.root,
        )
        if record.status != "not_applicable":
            self.save(record)
        return record

    def save(self, record: SourceRunFieldHealthRecord) -> SourceRunFieldHealthRecord:
        data = read_yaml(self.path, {"sources": {}})
        if not isinstance(data, dict):
            data = {"sources": {}}
        sources = data.setdefault("sources", {})
        if not isinstance(sources, dict):
            sources = {}
            data["sources"] = sources
        sources[record.source_id] = _record_as_mapping(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(self.path, data)
        return record

    def clear(self, source_id: str) -> None:
        data = read_yaml(self.path, {"sources": {}})
        if not isinstance(data, dict):
            return
        sources = data.get("sources")
        if not isinstance(sources, dict) or source_id not in sources:
            return
        sources.pop(source_id, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(self.path, data)


def evaluate_source_run_field_health(
    *,
    source_id: str,
    source_name: str,
    jobs: list[Job],
    run_id: str,
    recipe_path: str,
    root: Path,
) -> SourceRunFieldHealthRecord:
    if not recipe_path:
        return SourceRunFieldHealthRecord(
            source_id=source_id,
            source_name=source_name,
            checked_at=utc_now(),
            run_id=run_id,
            status="not_applicable",
            summary="Latest-run field health is only checked for recipe-backed sources.",
        )
    try:
        recipe = load_project_job_board_recipe(root, recipe_path)
    except (OSError, ValueError) as exc:
        return SourceRunFieldHealthRecord(
            source_id=source_id,
            source_name=source_name,
            checked_at=utc_now(),
            run_id=run_id,
            recipe_path=recipe_path,
            status="needs_relearn",
            summary=f"Could not load the selected reading plan: {exc}",
            recommended_action="reset_learned_state",
            action_href=f"/sources/{source_id}/learned-state/reset",
            warnings=[str(exc)],
        )
    expected_sources = expected_report_field_sources(recipe)
    expected_sources.setdefault("title", "listing.title_selector")
    expected_sources.setdefault("url", "listing.link_selector")
    expected_sources.setdefault("description", "job.description")
    expected_fields = sorted(expected_sources)
    coverage = {
        field_name: _coverage_for_field(jobs, field_name, source=expected_sources[field_name])
        for field_name in expected_fields
    }
    required_fields = set(CORE_REQUIRED_FIELDS)
    if "description" in expected_sources:
        required_fields.add("description")
    required_missing_fields = [
        field_name
        for field_name in sorted(required_fields)
        if coverage.get(field_name) and _required_missing(coverage[field_name])
    ]
    advisory_missing_fields = [
        field_name
        for field_name in expected_fields
        if field_name not in required_fields and coverage[field_name].present_count == 0
    ]
    description_stats = _description_stats(jobs)
    warnings: list[str] = []
    if advisory_missing_fields:
        warnings.append(
            "Configured fields missing from every latest-run job: " + ", ".join(advisory_missing_fields) + "."
        )
    description_expected = "description" in expected_sources
    description_needs_relearn = bool(
        description_expected
        and jobs
        and (
            description_stats["strong_count"] == 0
            or description_stats["present_count"] < max(1, math.ceil(len(jobs) * 0.5))
        )
    )
    if description_needs_relearn and "description" not in required_missing_fields:
        required_missing_fields.append("description")
    if description_expected and jobs and description_stats["strong_count"] < len(jobs):
        warnings.append(
            f"Only {description_stats['strong_count']}/{len(jobs)} latest-run job(s) had full descriptions."
        )
    status = _health_status(jobs, required_missing_fields, advisory_missing_fields)
    return SourceRunFieldHealthRecord(
        source_id=source_id,
        source_name=source_name,
        checked_at=utc_now(),
        run_id=run_id,
        recipe_path=recipe_path,
        job_count=len(jobs),
        expected_fields=expected_fields,
        field_coverage=coverage,
        required_missing_fields=sorted(set(required_missing_fields)),
        advisory_missing_fields=advisory_missing_fields,
        description_present_count=description_stats["present_count"],
        description_strong_count=description_stats["strong_count"],
        headline_only_description_count=description_stats["headline_only_count"],
        average_description_length=description_stats["average_length"],
        status=status,
        summary=_summary(status, len(jobs), sorted(set(required_missing_fields)), advisory_missing_fields),
        recommended_action="reset_learned_state" if status in {"needs_relearn", "warning"} else "",
        action_href=f"/sources/{source_id}/learned-state/reset" if status in {"needs_relearn", "warning"} else "",
        warnings=warnings,
    )


def _coverage_for_field(jobs: list[Job], field_name: str, *, source: str) -> SourceFieldCoverage:
    present_jobs = [job for job in jobs if _field_present(job, field_name)]
    sample_value = _field_value(present_jobs[0], field_name) if present_jobs else ""
    return SourceFieldCoverage(
        field=field_name,
        present_count=len(present_jobs),
        total_count=len(jobs),
        source=source,
        sample_value=sample_value[:160],
    )


def _required_missing(coverage: SourceFieldCoverage) -> bool:
    if coverage.total_count <= 0:
        return True
    if coverage.field in CORE_REQUIRED_FIELDS:
        return coverage.present_count < coverage.total_count
    return coverage.present_count == 0


def _field_present(job: Job, field_name: str) -> bool:
    value = getattr(job, field_name)
    if isinstance(value, list):
        return bool(value)
    text = str(value or "").strip()
    if field_name == "description":
        return _usable_description(text, job.title)
    return bool(text and text != "Not listed")


def _field_value(job: Job, field_name: str) -> str:
    value = getattr(job, field_name)
    if isinstance(value, list):
        return ", ".join(value)
    return str(value or "")


def _description_stats(jobs: list[Job]) -> dict[str, int]:
    descriptions = [str(job.description or "").strip() for job in jobs]
    meaningful_lengths = [
        len(description)
        for job, description in zip(jobs, descriptions, strict=False)
        if _usable_description(description, job.title)
    ]
    return {
        "present_count": len(meaningful_lengths),
        "strong_count": sum(1 for length in meaningful_lengths if length >= MIN_DESCRIPTION_CHARS),
        "headline_only_count": sum(
            1
            for job, description in zip(jobs, descriptions, strict=False)
            if _headline_only_description(description, job.title)
        ),
        "average_length": round(sum(meaningful_lengths) / len(meaningful_lengths)) if meaningful_lengths else 0,
    }


def _usable_description(description: str, title: str) -> bool:
    return len(description.strip()) >= 40 and not _headline_only_description(description, title)


def _headline_only_description(description: str, title: str) -> bool:
    normalized_description = _normalized(description)
    normalized_title = _normalized(title)
    if not normalized_description:
        return False
    if normalized_description == normalized_title:
        return True
    return len(normalized_description) < 80 and bool(
        normalized_title and (normalized_description in normalized_title or normalized_title in normalized_description)
    )


def _normalized(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _health_status(jobs: list[Job], required_missing_fields: list[str], advisory_missing_fields: list[str]) -> str:
    if not jobs:
        return "unknown"
    if required_missing_fields:
        return "needs_relearn"
    if advisory_missing_fields:
        return "warning"
    return "healthy"


def _summary(
    status: str,
    job_count: int,
    required_missing_fields: list[str],
    advisory_missing_fields: list[str],
) -> str:
    if status == "unknown":
        return "No latest-run jobs are available to check for this source yet."
    if status == "needs_relearn":
        fields = ", ".join(field.replace("_", " ") for field in required_missing_fields)
        return f"Latest run checked {job_count} job(s), but required field coverage failed: {fields}."
    if status == "warning":
        fields = ", ".join(field.replace("_", " ") for field in advisory_missing_fields)
        return f"Latest run checked {job_count} job(s). Some configured fields were missing from every job: {fields}."
    return f"Latest run checked {job_count} job(s); required fields were present."


def _latest_run_id(packages: list[dict[str, Any]]) -> str:
    run_ids = sorted({str(package.get("run_id") or "") for package in packages if package.get("run_id")})
    if run_ids:
        return run_ids[-1]
    return ""


def _latest_completed_daily_run(store: RunStore) -> RunRecord | None:
    try:
        runs = store.list_runs(include_tests=False)
    except ValueError:
        store.recover_corrupt_registry()
        runs = []
    return next((run for run in runs if run.status == "completed" and run.visibility == "active"), None)


def _refresh_summary(run_id: str, records: list[SourceRunFieldHealthRecord], skipped_package_count: int) -> str:
    needs_action = sum(1 for record in records if record.needs_action)
    warnings = sum(1 for record in records if record.status == "warning")
    healthy = sum(1 for record in records if record.status == "healthy")
    parts = [
        f"Checked latest-run field health for {len(records)} source(s) from run {run_id}:",
        f"{healthy} healthy",
        f"{warnings} warning",
        f"{needs_action} need relearning",
    ]
    if skipped_package_count:
        parts.append(f"{skipped_package_count} package(s) skipped")
    return "; ".join(parts) + "."


def _job_from_package(root: Path, package: dict[str, Any]) -> Job | None:
    job_path = str((package.get("paths") or {}).get("job") or "").strip()
    if not job_path:
        return None
    data = read_json(resolve_project_path(root, job_path), None)
    return Job.from_mapping(data) if isinstance(data, dict) else None


def _record_as_mapping(record: SourceRunFieldHealthRecord) -> dict[str, Any]:
    data = asdict(record)
    data["field_coverage"] = {field_name: asdict(coverage) for field_name, coverage in record.field_coverage.items()}
    return data


def _record_from_mapping(source_id: str, data: dict[str, Any]) -> SourceRunFieldHealthRecord:
    coverage_data = data.get("field_coverage", {})
    coverage = (
        {
            str(field_name): SourceFieldCoverage(
                field=str(value.get("field") or field_name),
                present_count=_int(value.get("present_count")),
                total_count=_int(value.get("total_count")),
                source=str(value.get("source") or ""),
                sample_value=str(value.get("sample_value") or ""),
            )
            for field_name, value in coverage_data.items()
            if isinstance(value, dict)
        }
        if isinstance(coverage_data, dict)
        else {}
    )
    return SourceRunFieldHealthRecord(
        source_id=source_id,
        source_name=str(data.get("source_name") or ""),
        checked_at=str(data.get("checked_at") or ""),
        run_id=str(data.get("run_id") or ""),
        recipe_path=str(data.get("recipe_path") or ""),
        job_count=_int(data.get("job_count")),
        expected_fields=_list(data.get("expected_fields")),
        field_coverage=coverage,
        required_missing_fields=_list(data.get("required_missing_fields")),
        advisory_missing_fields=_list(data.get("advisory_missing_fields")),
        description_present_count=_int(data.get("description_present_count")),
        description_strong_count=_int(data.get("description_strong_count")),
        headline_only_description_count=_int(data.get("headline_only_description_count")),
        average_description_length=_int(data.get("average_description_length")),
        status=str(data.get("status") or "unknown"),
        summary=str(data.get("summary") or "No latest-run field health has been checked for this source yet."),
        recommended_action=str(data.get("recommended_action") or ""),
        action_href=str(data.get("action_href") or ""),
        warnings=_list(data.get("warnings")),
    )


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []
