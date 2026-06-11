from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.config import ROOT, load_profile
from job_agent.digest import write_job_package, write_placeholder_job_package
from job_agent.generator import generate_materials
from job_agent.highlights import build_match_highlights
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.models import Job
from job_agent.paths import runtime_jobs_dir
from job_agent.run_store import RunEvent, RunOptions, RunRecord, RunStore, utc_now
from job_agent.scoring import score_job
from job_agent.services.ai_search_service import AiSearchEvaluation, AiSearchService, should_ai_evaluate_job
from job_agent.store import JobStore


@dataclass
class ManualPostingInput:
    title: str = ""
    source: str = ""
    company: str = ""
    url: str = ""
    application_url: str = ""
    location: str = ""
    remote: str = ""
    rate: str = ""
    workload: str = ""
    posted_date: str = ""
    description: str = ""
    ai_enhanced_search: bool = False
    generate_materials: bool = False
    use_llm: bool = False


@dataclass
class ManualPostingResult:
    run: RunRecord
    stable_id: str
    package_paths: dict[str, str]
    material_status: str


class ManualPostingService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.manual_jobs_path = runtime_jobs_dir(root) / "manual" / "manual_jobs.yaml"

    def import_posting(self, data: ManualPostingInput) -> ManualPostingResult:
        self.validate(data)
        job = self.to_job(data)
        self._append_manual_job(job)

        options = RunOptions(
            include_seen=True,
            ai_enhanced_search=data.ai_enhanced_search,
            generate_materials=data.generate_materials,
            use_llm=data.use_llm,
        )
        run_store = RunStore(self.root)
        run = run_store.create_run(options)
        run_store.update(run.run_id, status="running")
        self._append_event(run_store, run.run_id, "manual_posting_started", f"Manual posting imported: {job.title}")

        profile = load_profile(self.root)
        state = JobStore(self.root).classify([job])[0]
        app_status = ApplicationStatusStore(self.root).ensure_for_job(
            stable_id=state.stable_id,
            fuzzy_key=state.fuzzy_key,
            title=job.title,
            company=job.company,
            source=job.source,
            url=job.url,
            application_url=job.application_url,
        )
        match = score_job(job, profile)
        highlight_reasons = build_match_highlights(job, match, profile)
        ai_evaluation = self._maybe_ai_evaluate(
            data, job, match, profile, highlight_reasons, run.run_id, state.stable_id
        )

        if data.generate_materials:
            generated = generate_materials(
                job,
                match,
                profile,
                use_llm=data.use_llm,
                root=self.root,
                run_id=run.run_id,
                stable_id=state.stable_id,
            )
            paths = write_job_package(
                job,
                match,
                generated,
                date.today(),
                root=self.root,
                run_id=run.run_id,
                stable_id=state.stable_id,
                fuzzy_key=state.fuzzy_key,
                state=state.status,
                application_status=app_status.status,
                ai_evaluation=ai_evaluation.to_index_fields() if ai_evaluation.status != "missing" else None,
            )
            material_status = "generated"
        else:
            paths = write_placeholder_job_package(
                job,
                match,
                date.today(),
                root=self.root,
                run_id=run.run_id,
                stable_id=state.stable_id,
                fuzzy_key=state.fuzzy_key,
                state=state.status,
                application_status=app_status.status,
                ai_evaluation=ai_evaluation.to_index_fields() if ai_evaluation.status != "missing" else None,
            )
            material_status = "missing"

        run = run_store.update(
            run.run_id,
            status="completed",
            finished_at=utc_now(),
            total_loaded=1,
            new_roles=1 if state.status == "new" else 0,
            changed_roles=1 if state.status == "changed" else 0,
            strong_matches=1 if match.category == "strong" else 0,
            exploratory_matches=1 if match.category == "exploratory" else 0,
            weak_matches=1 if match.category == "weak" else 0,
            excluded_roles=1 if match.category == "excluded" else 0,
            generated_job_count=1 if match.category != "excluded" else 0,
        )
        self._append_event(run_store, run.run_id, "manual_posting_completed", f"Manual posting processed: {job.title}")
        return ManualPostingResult(
            run=run, stable_id=state.stable_id, package_paths=paths, material_status=material_status
        )

    @staticmethod
    def validate(data: ManualPostingInput) -> None:
        if not data.title.strip() and not data.description.strip():
            raise ValueError("Provide either a title or pasted job description.")

    def to_job(self, data: ManualPostingInput) -> Job:
        title = data.title.strip() or _title_from_description(data.description)
        return Job(
            title=title,
            company=data.company.strip() or "Unknown",
            recruiter=data.source.strip(),
            source=data.source.strip() or "Manual Intake",
            url=data.url.strip(),
            application_url=data.application_url.strip() or data.url.strip(),
            location=data.location.strip() or "Not listed",
            remote=data.remote.strip() or "Not listed",
            rate=data.rate.strip() or "Not listed",
            workload=data.workload.strip() or "Not listed",
            posted_date=data.posted_date.strip() or "Not listed",
            description=data.description.strip(),
            first_seen_date=str(date.today()),
            freshness_confidence="manual" if data.posted_date.strip() else "unknown",
            source_confidence="manual",
            raw_text=data.description.strip(),
            extraction_notes=["Manual posting intake; pasted text supplied by user."],
        )

    def _append_manual_job(self, job: Job) -> None:
        data = read_yaml(self.manual_jobs_path, {"jobs": []})
        jobs = data.get("jobs", [])
        jobs.append(asdict(job))
        write_yaml(self.manual_jobs_path, {"jobs": jobs})

    def _maybe_ai_evaluate(
        self,
        data: ManualPostingInput,
        job: Job,
        match,
        profile: dict[str, Any],
        highlight_reasons: list[str],
        run_id: str,
        stable_id: str,
    ) -> AiSearchEvaluation:
        if not data.ai_enhanced_search or not should_ai_evaluate_job(job, match, profile, highlight_reasons):
            return AiSearchEvaluation(status="missing")
        service = AiSearchService(self.root)
        if not service.is_configured():
            return service.skipped("ANTHROPIC_API_KEY is missing or placeholder.")
        try:
            return service.evaluate(job, match, profile, highlight_reasons, run_id=run_id, stable_id=stable_id)
        except Exception as exc:
            return service.failed(str(exc))

    @staticmethod
    def _append_event(run_store: RunStore, run_id: str, event_type: str, message: str) -> None:
        run_store.append_event(RunEvent(run_id=run_id, event_type=event_type, message=message, phase="manual_intake"))


def _title_from_description(description: str) -> str:
    first_line = next((line.strip() for line in description.splitlines() if line.strip()), "Manual job posting")
    return first_line[:120]
