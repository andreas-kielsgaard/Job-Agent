from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .application_status_store import ApplicationStatusStore
from .config import ROOT, load_profile
from .digest import write_daily_digest, write_excluded_summary, write_job_package, write_placeholder_job_package
from .generator import generate_materials
from .highlights import build_match_highlights
from .run_store import RunEvent, RunOptions, RunRecord, RunStore, utc_now
from .scoring import score_job
from .services.ai_search_service import AiSearchEvaluation, AiSearchService, should_ai_evaluate_job
from .sources import SourceFetchResult, SourceProgressEvent, iter_source_results
from .store import JobStore
from .token_usage import TokenUsageStore

ProgressCallback = Callable[[RunEvent], None]


@dataclass
class RunResult:
    record: RunRecord
    digest_items: list[dict]
    excluded_items: list[dict]
    source_warnings: list = field(default_factory=list)


def run_daily_agent(
    options: RunOptions,
    progress_callback: ProgressCallback | None = None,
    root: Path = ROOT,
    run_id: str | None = None,
    source_id: str = "",
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
        emit(
            "run_started",
            f"Single-source run started for {source_id}." if source_id else "Daily agent run started.",
            "startup",
        )

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

        store = JobStore(root)
        status_store = ApplicationStatusStore(root)
        ai_search_service = AiSearchService(root) if options.ai_enhanced_search else None
        ai_search_missing_key_warned = False
        threshold = int(profile.get("thresholds", {}).get("minimum_digest_score", 45))
        run_date = date.today()
        all_warnings = []
        total_loaded = 0
        digest_items: list[dict] = []
        excluded_items: list[dict] = []
        processed_states = []
        processed_keys: set[str] = set()

        for source_fetch in iter_source_results(root, progress_callback=emit_source_progress, source_id=source_id):
            total_loaded += len(source_fetch.result.jobs)
            all_warnings.extend(source_fetch.result.warnings)
            source_states = store.classify(source_fetch.result.jobs)
            candidate_states = (
                source_states
                if options.include_seen
                else [state for state in source_states if state.status in {"new", "changed"}]
            )
            source_counts = _new_source_counts(source_fetch)

            for index, state in enumerate(candidate_states, start=1):
                duplicate_key = state.stable_id
                fuzzy_duplicate_key = f"fuzzy:{state.fuzzy_key}"
                if duplicate_key in processed_keys or fuzzy_duplicate_key in processed_keys:
                    emit(
                        "job_duplicate_skipped",
                        f"Skipped duplicate job from this run: {state.job.title}.",
                        "classification",
                        current_source=source_fetch.source_name,
                        current_job=state.job.title,
                        counts={
                            "source_index": source_fetch.source_index,
                            "source_count": source_fetch.source_count,
                        },
                    )
                    source_counts["duplicates_skipped"] += 1
                    continue
                processed_keys.add(duplicate_key)
                processed_keys.add(fuzzy_duplicate_key)

                job = state.job
                emit(
                    "job_classified",
                    f"{job.title} classified as {state.status}.",
                    "classification",
                    current_source=source_fetch.source_name,
                    current_job=job.title,
                    counts={
                        "processed": index,
                        "candidate_jobs": len(candidate_states),
                        "source_index": source_fetch.source_index,
                        "source_count": source_fetch.source_count,
                    },
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
                    current_source=source_fetch.source_name,
                    current_job=job.title,
                )
                item = {"job": job, "match": match, "state": state, "application_status": app_status}

                should_include = (
                    match.category in {"strong", "exploratory"}
                    and match.total_score >= threshold
                    and match.category != "excluded"
                ) or options.include_weak

                highlight_reasons = []
                if match.category in {"strong", "exploratory"} or (should_include and match.category != "excluded"):
                    highlight_reasons = build_match_highlights(job, match, profile)
                if highlight_reasons:
                    source_counts["highlighted_matches"] += 1
                    emit(
                        "match_highlight",
                        _match_highlight_message(job.title, match.total_score, highlight_reasons),
                        "scoring",
                        current_source=source_fetch.source_name,
                        current_job=job.title,
                        counts={
                            "score": match.total_score,
                            "source_index": source_fetch.source_index,
                            "source_count": source_fetch.source_count,
                            "highlight_count": len(highlight_reasons),
                        },
                    )
                ai_evaluation = AiSearchEvaluation(status="missing")
                if options.ai_enhanced_search and should_ai_evaluate_job(job, match, profile, highlight_reasons):
                    if ai_search_service and not ai_search_service.is_configured():
                        ai_evaluation = ai_search_service.skipped("ANTHROPIC_API_KEY is missing or placeholder.")
                        if not ai_search_missing_key_warned:
                            ai_search_missing_key_warned = True
                            emit(
                                "ai_evaluation_skipped",
                                "AI-enhanced search skipped because ANTHROPIC_API_KEY is missing or placeholder.",
                                "scoring",
                                current_source=source_fetch.source_name,
                                current_job=job.title,
                                counts={
                                    "score": match.total_score,
                                    "source_index": source_fetch.source_index,
                                    "source_count": source_fetch.source_count,
                                },
                            )
                    elif ai_search_service:
                        emit(
                            "ai_evaluation_started",
                            f"AI relevance summary started: {job.title}.",
                            "scoring",
                            current_source=source_fetch.source_name,
                            current_job=job.title,
                            counts={
                                "score": match.total_score,
                                "source_index": source_fetch.source_index,
                                "source_count": source_fetch.source_count,
                            },
                        )
                        try:
                            ai_evaluation = ai_search_service.evaluate(
                                job,
                                match,
                                profile,
                                highlight_reasons,
                                run_id=run_id,
                                stable_id=state.stable_id,
                            )
                            source_counts["ai_evaluations_completed"] += 1
                            if ai_evaluation.should_prioritize:
                                source_counts["ai_prioritized"] += 1
                            emit(
                                "ai_evaluation_completed",
                                (
                                    f"AI relevance summary completed: {job.title} - "
                                    f"{ai_evaluation.fit_confidence or 'medium'} confidence"
                                ),
                                "scoring",
                                current_source=source_fetch.source_name,
                                current_job=job.title,
                                counts={
                                    "score": match.total_score,
                                    "source_index": source_fetch.source_index,
                                    "source_count": source_fetch.source_count,
                                    "should_prioritize": int(ai_evaluation.should_prioritize),
                                },
                            )
                        except Exception as exc:
                            ai_evaluation = ai_search_service.failed(str(exc))
                            source_counts["ai_evaluations_failed"] += 1
                            emit(
                                "ai_evaluation_failed",
                                f"AI relevance summary failed for {job.title}: {exc}",
                                "scoring",
                                current_source=source_fetch.source_name,
                                current_job=job.title,
                                counts={
                                    "score": match.total_score,
                                    "source_index": source_fetch.source_index,
                                    "source_count": source_fetch.source_count,
                                },
                            )

                source_counts["candidates_processed"] += 1
                if state.status == "new":
                    source_counts["new_roles"] += 1
                elif state.status == "changed":
                    source_counts["changed_roles"] += 1
                if match.category == "strong":
                    source_counts["strong_matches"] += 1
                elif match.category == "exploratory":
                    source_counts["exploratory_matches"] += 1
                elif match.category == "weak":
                    source_counts["weak_matches"] += 1
                elif match.category == "excluded":
                    source_counts["excluded_roles"] += 1

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
                            ai_evaluation=ai_evaluation.to_index_fields()
                            if ai_evaluation.status != "missing"
                            else None,
                        )
                        emit(
                            "package_generated",
                            f"Generated package for {job.title}.",
                            "generation",
                            current_source=source_fetch.source_name,
                            current_job=job.title,
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
                            ai_evaluation=ai_evaluation.to_index_fields()
                            if ai_evaluation.status != "missing"
                            else None,
                        )
                        emit(
                            "package_skipped",
                            f"Skipped material generation for {job.title}.",
                            "generation",
                            current_source=source_fetch.source_name,
                            current_job=job.title,
                        )
                    item["paths"] = paths
                    digest_items.append(item)
                    source_counts["included_roles"] += 1
                else:
                    excluded_items.append(item)
                processed_states.append(state)

            emit(
                "source_processed",
                _source_processed_message(source_fetch, source_counts),
                "source_processing",
                current_source=source_fetch.source_name,
                counts=source_counts,
            )

        emit(
            "jobs_loaded",
            f"Loaded {total_loaded} jobs.",
            "source_ingestion",
            counts={"total_loaded": total_loaded},
        )

        summary = {
            "total_loaded": total_loaded,
            "new_roles": sum(1 for state in processed_states if state.status == "new"),
            "changed_roles": sum(1 for state in processed_states if state.status == "changed"),
            "strong_matches": sum(1 for item in digest_items if item["match"].category == "strong"),
            "exploratory_matches": sum(1 for item in digest_items if item["match"].category == "exploratory"),
            "weak_matches": sum(1 for item in excluded_items if item["match"].category == "weak"),
            "excluded_roles": sum(1 for item in excluded_items if item["match"].category == "excluded"),
            "source_warnings": len(all_warnings),
        }

        digest_path = write_daily_digest(summary, digest_items, all_warnings, run_date, root=root)
        excluded_path = write_excluded_summary(excluded_items, all_warnings, run_date, root=root)
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
        emit(
            "run_completed",
            f"Single-source run completed for {source_id}." if source_id else "Daily agent run completed.",
            "complete",
            status="completed",
            counts=summary,
        )
        return RunResult(record=record, digest_items=digest_items, excluded_items=excluded_items, source_warnings=all_warnings)
    except Exception as exc:
        record = run_store.update(run_id, status="failed", finished_at=utc_now(), error_message=str(exc))
        emit("run_failed", f"Daily agent run failed: {exc}", "failed", status="failed")
        raise


def _store_nested_event(run_store: RunStore, event: RunEvent, progress_callback: ProgressCallback | None) -> None:
    run_store.append_event(event)
    if progress_callback:
        progress_callback(event)


def _new_source_counts(source_fetch: SourceFetchResult) -> dict:
    return {
        "source_index": source_fetch.source_index,
        "source_count": source_fetch.source_count,
        "jobs_found": len(source_fetch.result.jobs),
        "warnings_count": len(source_fetch.result.warnings),
        "new_roles": 0,
        "changed_roles": 0,
        "candidates_processed": 0,
        "strong_matches": 0,
        "exploratory_matches": 0,
        "weak_matches": 0,
        "excluded_roles": 0,
        "included_roles": 0,
        "duplicates_skipped": 0,
        "highlighted_matches": 0,
        "ai_evaluations_completed": 0,
        "ai_evaluations_failed": 0,
        "ai_prioritized": 0,
    }


def _source_processed_message(source_fetch: SourceFetchResult, counts: dict) -> str:
    changed_text = counts["new_roles"] + counts["changed_roles"]
    return (
        f"Processed source {source_fetch.source_index}/{source_fetch.source_count}: {source_fetch.source_name} - "
        f"{counts['jobs_found']} jobs, {changed_text} new/changed, "
        f"{counts['strong_matches']} strong, {counts['exploratory_matches']} exploratory, "
        f"{counts['highlighted_matches']} highlights, {counts['ai_evaluations_completed']} AI summaries"
    )


def _match_highlight_message(job_title: str, score: int, reasons: list[str]) -> str:
    return f"Highlighted match: {job_title} - {score}% - {'; '.join(reasons[:4])}"
