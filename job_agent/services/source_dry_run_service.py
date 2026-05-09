from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from job_agent.config import ROOT
from job_agent.models import Job
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.sources import adapter_for_source


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


class SourceDryRunService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.execution_sources = ExecutionSourceService(root)

    def dry_run(self, source_id: str, *, force_disabled: bool = False) -> SourceDryRunResult:
        source_id = source_id.strip()
        source = self.execution_sources.find_by_source_id(source_id)
        if not source:
            return SourceDryRunResult(source_id=source_id, status="not_found")

        source_name = str(source.get("name") or source_id)
        source_type = str(source.get("type") or "")
        enabled = bool(source.get("enabled", True))
        if not enabled and not force_disabled:
            return SourceDryRunResult(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                source_enabled=False,
                status="disabled",
            )

        try:
            result = adapter_for_source(source, self.root).fetch()
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
        description_preview=" ".join(job.description.split())[:240],
        extraction_notes=list(job.extraction_notes),
    )
