from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .application_status_store import ApplicationStatusStore
from .config import ROOT, load_profile
from .digest import write_daily_digest, write_excluded_summary, write_job_package, write_placeholder_job_package
from .generator import generate_materials
from .run_store import RunEvent, RunOptions, RunRecord, RunStore, utc_now
from .scoring import score_job
from .sources import SourceProgressEvent, load_jobs_from_sources
from .store import JobStore
from .token_usage import TokenUsageStore

ProgressCallback = Callable[[RunEvent], None]


@dataclass
class RunResult:
    record: RunRecord
    digest_items: list[dict]
    excluded_items: list[dict]


def run_daily_agent(
    options: RunOptions,
    progress_callback: ProgressCallback | None = None,
    root: Path = ROOT,
    run_id: str | None = None,
) -> RunResult:
    run_store = RunStore(root)
    record = run_store.get(run_id) if run_id else None
    if record is None:
        record = run_store.create_run(options)
    run_id = record.run_id

    def emit(
        event_type: str,
        message: str,
        phase: str = "",
        status: str = "running",
        current_source: str = "",
        current_job: str = "",
        counts: dict | None = None,
    ) -> None:
        event = RunEvent(
            run_id=run_id,
            event_type=event_type,
            message=message,
            phase=phase,
            status=status,
            current_source=current_source,
            current_job=current_job,
            counts=counts or {},
        )
        run_store.append_event(event)
        if progress_callback:
            progress_callback(event)

    try:
        run_store.update(run_id, status="running")
        emit("run_started", "Daily agent run started.", "startup")

        profile = load_profile(root)
        emit("profile_loaded", "Profile loaded.", "startup")

        def emit_source_progress(event: SourceProgressEvent) -> None:
            counts = {
                "source_index": event.source_index,
                "source_count": event.source_count,
                "jobs_found": event.jobs_found,
                "warnings_count": event.warnings_count,
            }
            if event.elapsed_time_seconds is not None:
                counts["elapsed_time_seconds"] = event.elapsed_time_seconds
            emit(
                event.event_type,
                event.message,
                "source_ingestion",
                current_source=event.source_name,
                counts=counts,
            )

        source_result = load_jobs_from_sources(root, progress_callback=emit_source_progress)
        emit(
            "jobs_loaded",
            f"Loaded {len(source_result.jobs)} jobs.",
            "source_ingestion",
            counts={"total_loaded": len(source_result.jobs)},
        )

        store = JobStore(root)
        status_store = ApplicationStatusStore(root)
        states = store.classify(source_result.jobs)
        candidate_states = (
            states if options.include_seen else [state for state in states if state.status in {"new", "changed"}]
        )
        threshold = int(profile.get("thresholds", {}).get("minimum_digest_score", 45))
        run_date = date.today()
        digest_items: list[dict] = []
        excluded_items: list[dict] = []
        processed_states = []

        for index, state in enumerate(candidate_states, start=1):
            job = state.job
            emit(
                "job_classified",
                f"{job.title} classified as {state.status}.",
                "classification",
                current_job=job.title,
                counts={"processed": index, "candidate_jobs": len(candidate_states)},
            )
            app_status = status_store.ensure_for_job(
                stable_id=state.stable_id,
                fuzzy_key=state.fuzzy_key,
                title=job.title,
                company=job.company,
                source=job.source,
                url=job.url,
                application_url=job.application_url,
            )
            match = score_job(job, profile)
            emit(
                "job_scored",
                f"{job.title} scored {match.total_score}% ({match.category}).",
                "scoring",
                current_job=job.title,
            )
            item = {"job": job, "match": match, "state": state, "application_status": app_status}

            should_include = (
                match.category in {"strong", "exploratory"}
                and match.total_score >= threshold
                and match.category != "excluded"
            ) or options.include_weak

            if should_include and match.category != "excluded":
                if options.generate_materials:
                    package = generate_materials(
                        job,
                        match,
                        profile,
                        use_llm=options.use_llm,
                        root=root,
                        run_id=run_id,
                        stable_id=state.stable_id,
                        progress_callback=lambda event: _store_nested_event(run_store, event, progress_callback),
                    )
                    paths = write_job_package(
                        job,
                        match,
                        package,
                        run_date,
                        root=root,
                        run_id=run_id,
                        stable_id=state.stable_id,
                        fuzzy_key=state.fuzzy_key,
                        state=state.status,
                        application_status=app_status.status,
                    )
                    emit(
                        "package_generated", f"Generated package for {job.title}.", "generation", current_job=job.title
                    )
                else:
                    paths = write_placeholder_job_package(
                        job,
                        match,
                        run_date,
                        root=root,
                        run_id=run_id,
                        stable_id=state.stable_id,
                        fuzzy_key=state.fuzzy_key,
                        state=state.status,
                        application_status=app_status.status,
                    )
                    emit(
                        "package_skipped",
                        f"Skipped material generation for {job.title}.",
                        "generation",
                        current_job=job.title,
                    )
                item["paths"] = paths
                digest_items.append(item)
            else:
                excluded_items.append(item)
            processed_states.append(state)

        summary = {
            "total_loaded": len(source_result.jobs),
            "new_roles": sum(1 for state in states if state.status == "new"),
            "changed_roles": sum(1 for state in states if state.status == "changed"),
            "strong_matches": sum(1 for item in digest_items if item["match"].category == "strong"),
            "exploratory_matches": sum(1 for item in digest_items if item["match"].category == "exploratory"),
            "weak_matches": sum(1 for item in excluded_items if item["match"].category == "weak"),
            "excluded_roles": sum(1 for item in excluded_items if item["match"].category == "excluded"),
            "source_warnings": len(source_result.warnings),
        }

        digest_path = write_daily_digest(summary, digest_items, source_result.warnings, run_date, root=root)
        excluded_path = write_excluded_summary(excluded_items, source_result.warnings, run_date, root=root)
        if options.mark_seen and not options.is_test:
            store.mark_seen(processed_states)
            emit("seen_marked", "Processed jobs marked as seen.", "finalize")
        elif options.mark_seen and options.is_test:
            emit("seen_mark_skipped", "Test run: processed jobs were not marked as seen.", "finalize")

        token_summary = TokenUsageStore(root).summarize(run_id)
        record = run_store.update(
            run_id,
            status="completed",
            finished_at=utc_now(),
            total_loaded=summary["total_loaded"],
            new_roles=summary["new_roles"],
            changed_roles=summary["changed_roles"],
            strong_matches=summary["strong_matches"],
            exploratory_matches=summary["exploratory_matches"],
            weak_matches=summary["weak_matches"],
            excluded_roles=summary["excluded_roles"],
            source_warnings=summary["source_warnings"],
            generated_job_count=len(digest_items),
            digest_path=str(digest_path),
            excluded_path=str(excluded_path),
            token_usage=token_summary,
            total_estimated_llm_cost=token_summary.get("estimated_cost"),
        )
        emit("run_completed", "Daily agent run completed.", "complete", status="completed", counts=summary)
        return RunResult(record=record, digest_items=digest_items, excluded_items=excluded_items)
    except Exception as exc:
        record = run_store.update(run_id, status="failed", finished_at=utc_now(), error_message=str(exc))
        emit("run_failed", f"Daily agent run failed: {exc}", "failed", status="failed")
        raise


def _store_nested_event(run_store: RunStore, event: RunEvent, progress_callback: ProgressCallback | None) -> None:
    run_store.append_event(event)
    if progress_callback:
        progress_callback(event)
