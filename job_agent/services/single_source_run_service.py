from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from job_agent.config import ROOT
from job_agent.run_service import run_daily_agent
from job_agent.run_store import RunOptions
from job_agent.services.execution_source_service import ExecutionSourceService


@dataclass
class SingleSourcePackagePreview:
    title: str
    source_id: str = ""
    match_score: int = 0
    match_category: str = ""
    package_path: str = ""
    job_url: str = ""


@dataclass
class SingleSourceRunResult:
    source_id: str
    source_name: str = ""
    source_type: str = ""
    status: str = "not_found"
    run_id: str = ""
    extracted_job_count: int = 0
    package_count: int = 0
    strong_matches: int = 0
    exploratory_matches: int = 0
    warnings: list[str] = field(default_factory=list)
    run_detail_url: str = ""
    packages: list[SingleSourcePackagePreview] = field(default_factory=list)


class SingleSourceRunService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.execution_sources = ExecutionSourceService(root)

    def run(self, source_id: str) -> SingleSourceRunResult:
        source_id = source_id.strip()
        source = self.execution_sources.find_by_source_id(source_id)
        if not source:
            return SingleSourceRunResult(source_id=source_id, status="not_found")
        source_name = str(source.get("name") or source_id)
        source_type = str(source.get("type") or "")
        if not bool(source.get("enabled", True)):
            return SingleSourceRunResult(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                status="disabled",
            )

        try:
            result = run_daily_agent(
                RunOptions(
                    include_seen=False,
                    include_weak=False,
                    mark_seen=False,
                    generate_materials=False,
                    use_llm=False,
                    ai_enhanced_search=False,
                ),
                root=self.root,
                source_id=source_id,
            )
        except Exception as exc:
            return SingleSourceRunResult(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                status="failed",
                warnings=[str(exc)],
            )
        record = result.record
        warnings = [str(warning.message) for warning in _source_warnings(result)]
        status = "completed_with_warnings" if warnings else record.status
        return SingleSourceRunResult(
            source_id=source_id,
            source_name=source_name,
            source_type=source_type,
            status=status,
            run_id=record.run_id,
            extracted_job_count=record.total_loaded,
            package_count=len(result.digest_items),
            strong_matches=record.strong_matches,
            exploratory_matches=record.exploratory_matches,
            warnings=warnings,
            run_detail_url=f"/runs/{record.run_id}",
            packages=[_package_preview(item) for item in result.digest_items],
        )


def _source_warnings(result) -> list:
    return list(result.source_warnings or [])


def _package_preview(item: dict) -> SingleSourcePackagePreview:
    job = item["job"]
    match = item["match"]
    paths = item.get("paths", {})
    return SingleSourcePackagePreview(
        title=job.title,
        source_id=job.source_id,
        match_score=match.total_score,
        match_category=match.category,
        package_path=str(paths.get("index") or paths.get("job") or ""),
        job_url=job.url,
    )
