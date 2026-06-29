from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .application_status_store import ApplicationStatusStore
from .config import ROOT, load_profile
from .digest import write_daily_digest, write_excluded_summary, write_job_package, write_placeholder_job_package
from .generator import generate_materials
from .highlights import build_match_highlights
from .models import JobState, SourceWarning
from .paths import resolve_project_path
from .run_store import RunEvent, RunOptions, RunRecord, RunStore, utc_now
from .scoring import score_job
from .services.ai_search_service import AiSearchEvaluation, AiSearchService, should_ai_evaluate_job
from .services.job_board_recipe_service import enrich_jobs_with_detail_pages_with_trace
from .services.recipes.mapping import load_project_job_board_recipe
from .services.source_run_field_health_service import SourceRunFieldHealthService
from .services.source_session_service import SourceSessionService
from .sources import SourceFetchOptions, SourceFetchResult, SourceProgressEvent, iter_source_results
from .store import JobStore
from .token_usage import TokenUsageStore

ProgressCallback = Callable[[RunEvent], None]


@dataclass
class RunResult:
    record: RunRecord
    digest_items: list[dict]
    excluded_items: list[dict]
    source_warnings: list = field(default_factory=list)


@dataclass
class DetailReviewResult:
    states: list[JobState]
    warnings: list[SourceWarning] = field(default_factory=list)
    attempts: int = 0
    enriched: int = 0
    skipped_for_limit: int = 0


def run_daily_agent(
    options: RunOptions,
    progress_callback: ProgressCallback | None = None,
    root: Path = ROOT,
    run_id: str | None = None,
    source_id: str = "",
    include_disabled_source: bool = False,
    append_to_existing: bool = False,
) -> RunResult:
    run_store = RunStore(root)
    record = run_store.get(run_id) if run_id else None
    if record is None:
        record = run_store.create_run(options)
    run_id = record.run_id
    existing_totals = _record_totals(record) if append_to_existing else {}
    include_disabled = include_disabled_source or options.include_disabled_sources
    run_label = _run_label(options, source_id)

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
            f"{run_label} started.",
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
            counts.update(event.details or {})
            if event.elapsed_time_seconds is not None:
                counts["elapsed_time_seconds"] = event.elapsed_time_seconds
            if event.page_explored_count:
                counts["page_explored_count"] = event.page_explored_count
            if event.page_total:
                counts["page_total"] = event.page_total
            if event.event_type == "source_activity":
                counts["activity"] = 1
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
        ai_search_configured = bool(ai_search_service and ai_search_service.is_configured())
        ai_search_missing_key_warned = False
        threshold = int(profile.get("thresholds", {}).get("minimum_digest_score", 45))
        run_date = date.today()
        all_warnings = []
        total_loaded = 0
        digest_items: list[dict] = []
        excluded_items: list[dict] = []
        processed_states = []
        processed_keys: set[str] = set()
        max_parallel_sources = _max_parallel_sources_from_profile(profile)
        source_access_purpose = _source_access_purpose(options, source_id)
        max_parallel_ai_matches = _max_parallel_ai_matches_from_profile(profile)
        fetch_options = SourceFetchOptions(
            fetch_details=False,
            use_source_job_limit=False,
            use_recipe_card_limit=False,
            pagination_page_limit=0,
            enforce_saved_readiness=True,
            require_setup_complete=not bool(source_id),
            max_parallel_sources=max_parallel_sources,
            access_purpose=source_access_purpose,
            wait_for_source_access=options.wait_for_source_access,
            source_access_wait_timeout_seconds=options.source_access_wait_timeout_seconds,
            source_access_wait_poll_seconds=options.source_access_wait_poll_seconds,
        )
        if options.full_source_ingestion:
            emit(
                "full_source_ingestion_configured",
                (
                    "Full source ingestion will read all sources that are currently eligible to execute, including "
                    "sources excluded from daily runs, and will not stop at the default detail-page cap."
                ),
                "startup",
                counts={
                    "detail_pause_every_jobs": max(0, int(options.detail_pause_every_jobs or 0)),
                    "detail_pause_seconds": max(0, int(options.detail_pause_seconds or 0)),
                },
            )
        emit(
            "source_parallelism_configured",
            (
                f"Using up to {max_parallel_sources} parallel source lane(s); "
                "same-host source requests remain serialized."
            ),
            "startup",
            counts={"max_parallel_sources": max_parallel_sources},
        )
        if options.ai_enhanced_search:
            emit(
                "ai_parallelism_configured",
                f"Using up to {max_parallel_ai_matches} parallel LLM match lane(s).",
                "startup",
                counts={"max_parallel_ai_matches": max_parallel_ai_matches},
            )

        for source_fetch in iter_source_results(
            root,
            progress_callback=emit_source_progress,
            source_id=source_id,
            include_disabled=include_disabled,
            fetch_options=fetch_options,
        ):
            total_loaded += len(source_fetch.result.jobs)
            all_warnings.extend(source_fetch.result.warnings)
            source_states = store.classify(source_fetch.result.jobs, identity_only=True)
            candidate_listing_states = (
                source_states
                if options.include_seen
                else [state for state in source_states if state.status in {"new", "changed"}]
            )
            source_counts = _new_source_counts(source_fetch)
            status_counts = _state_status_counts(source_states)
            source_counts["new_candidates"] = status_counts["new"]
            source_counts["changed_candidates"] = status_counts["changed"]
            source_counts["previously_seen"] = status_counts["previously_seen"]
            source_counts["previously_seen_skipped"] = 0 if options.include_seen else status_counts["previously_seen"]
            source_detail_budget = options.detail_extraction_limit
            detail_review = _prepare_detail_review(
                source_fetch,
                candidate_listing_states,
                store,
                root,
                source_detail_budget,
                emit,
                pause_every_jobs=options.detail_pause_every_jobs if options.full_source_ingestion else 0,
                pause_seconds=options.detail_pause_seconds if options.full_source_ingestion else 0,
            )
            candidate_states = detail_review.states
            all_warnings.extend(detail_review.warnings)
            source_counts["warnings_count"] += len(detail_review.warnings)
            source_counts["detail_fetch_count"] = detail_review.attempts
            source_counts["detail_enriched_count"] = detail_review.enriched
            source_counts["detail_limit_skipped_count"] = detail_review.skipped_for_limit
            source_counts["reviewed_in_detail_count"] = len(candidate_states)
            remaining_source_detail_budget = source_detail_budget
            if source_detail_budget is not None and _source_follows_detail_pages(source_fetch):
                remaining_source_detail_budget = max(0, source_detail_budget - len(candidate_states))
            if detail_review.skipped_for_limit:
                emit(
                    "detail_limit_reached",
                    (
                        f"Detail review limit reached for {source_fetch.source_name}: "
                        f"{detail_review.skipped_for_limit} new job(s) left for the next run."
                    ),
                    "source_processing",
                    current_source=source_fetch.source_name,
                    counts={
                        "source_index": source_fetch.source_index,
                        "source_count": source_fetch.source_count,
                        "detail_limit_skipped_count": detail_review.skipped_for_limit,
                        "remaining_detail_budget": remaining_source_detail_budget or 0,
                    },
                )

            source_items: list[dict] = []
            ai_futures = {}
            ai_executor: ThreadPoolExecutor | None = None
            try:
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
                    item = {
                        "job": job,
                        "match": match,
                        "state": state,
                        "application_status": app_status,
                        "should_include": should_include,
                        "ai_evaluation": ai_evaluation,
                    }
                    source_items.append(item)

                    if options.ai_enhanced_search and should_ai_evaluate_job(job, match, profile, highlight_reasons):
                        if ai_search_service and not ai_search_configured:
                            ai_evaluation = ai_search_service.skipped("ANTHROPIC_API_KEY is missing or placeholder.")
                            item["ai_evaluation"] = ai_evaluation
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
                            if ai_executor is None:
                                ai_executor = ThreadPoolExecutor(
                                    max_workers=max_parallel_ai_matches,
                                    thread_name_prefix="run-ai-match",
                                )
                            future = ai_executor.submit(
                                _evaluate_ai_match,
                                root,
                                job,
                                match,
                                profile,
                                highlight_reasons,
                                run_id,
                                state.stable_id,
                                options.llm_model,
                            )
                            ai_futures[future] = item

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
                    processed_states.append(state)

                for future in as_completed(ai_futures):
                    item = ai_futures[future]
                    job = item["job"]
                    match = item["match"]
                    state = item["state"]
                    try:
                        ai_evaluation = future.result()
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
                        ai_evaluation = AiSearchService(root).failed(str(exc))
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
                    item["ai_evaluation"] = ai_evaluation
            finally:
                if ai_executor:
                    ai_executor.shutdown(wait=True, cancel_futures=False)

            for item in source_items:
                job = item["job"]
                match = item["match"]
                state = item["state"]
                app_status = item["application_status"]
                ai_evaluation = item["ai_evaluation"]
                should_include = bool(item["should_include"])
                index_ai_evaluation = ai_evaluation.to_index_fields() if ai_evaluation.status != "missing" else None
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
                            llm_model=options.llm_model,
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
                            ai_evaluation=index_ai_evaluation,
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
                            ai_evaluation=index_ai_evaluation,
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
                        ai_evaluation=index_ai_evaluation,
                        review_list=False,
                    )
                    item["paths"] = paths
                    excluded_items.append(item)

            emit(
                "source_processed",
                _source_processed_message(source_fetch, source_counts),
                "source_processing",
                current_source=source_fetch.source_name,
                counts=source_counts,
            )

        _record_source_run_field_health(
            processed_states,
            run_id=run_id,
            root=root,
            all_warnings=all_warnings,
            emit=emit,
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

        if append_to_existing and record.digest_path:
            digest_path = Path(record.digest_path)
            excluded_path = (
                Path(record.excluded_path)
                if record.excluded_path
                else write_excluded_summary(excluded_items, all_warnings, run_date, root=root)
            )
        else:
            digest_path = write_daily_digest(summary, digest_items, all_warnings, run_date, root=root)
            excluded_path = write_excluded_summary(excluded_items, all_warnings, run_date, root=root)
        if options.mark_seen and not options.is_test:
            store.mark_seen(processed_states)
            emit("seen_marked", "Processed jobs marked as seen.", "finalize")
        elif options.mark_seen and options.is_test:
            emit("seen_mark_skipped", "Test run: processed jobs were not marked as seen.", "finalize")

        token_summary = TokenUsageStore(root).summarize(run_id)
        summary_for_record = _summary_for_record(summary, existing_totals, len(digest_items))
        record = run_store.update(
            run_id,
            status="completed",
            finished_at=utc_now(),
            total_loaded=summary_for_record["total_loaded"],
            new_roles=summary_for_record["new_roles"],
            changed_roles=summary_for_record["changed_roles"],
            strong_matches=summary_for_record["strong_matches"],
            exploratory_matches=summary_for_record["exploratory_matches"],
            weak_matches=summary_for_record["weak_matches"],
            excluded_roles=summary_for_record["excluded_roles"],
            source_warnings=summary_for_record["source_warnings"],
            generated_job_count=summary_for_record["generated_job_count"],
            digest_path=str(digest_path),
            excluded_path=str(excluded_path),
            token_usage=token_summary,
            total_estimated_llm_cost=token_summary.get("estimated_cost"),
        )
        emit(
            "run_completed",
            f"{run_label} completed.",
            "complete",
            status="completed",
            counts=summary,
        )
        return RunResult(
            record=record, digest_items=digest_items, excluded_items=excluded_items, source_warnings=all_warnings
        )
    except Exception as exc:
        record = run_store.update(run_id, status="failed", finished_at=utc_now(), error_message=str(exc))
        emit("run_failed", f"Daily agent run failed: {exc}", "failed", status="failed")
        raise


def _store_nested_event(run_store: RunStore, event: RunEvent, progress_callback: ProgressCallback | None) -> None:
    run_store.append_event(event)
    if progress_callback:
        progress_callback(event)


def _record_source_run_field_health(
    processed_states: list[JobState],
    *,
    run_id: str,
    root: Path,
    all_warnings: list[SourceWarning],
    emit: Callable[..., None],
) -> None:
    jobs_by_source: dict[str, list] = {}
    for state in processed_states:
        source_id = str(state.job.source_id or "").strip()
        if not source_id:
            continue
        jobs_by_source.setdefault(source_id, []).append(state.job)
    if not jobs_by_source:
        return
    service = SourceRunFieldHealthService(root)
    for source_id, jobs in sorted(jobs_by_source.items()):
        source_name = str(jobs[0].source or source_id)
        try:
            record = service.update_from_jobs(source_id, source_name=source_name, jobs=jobs, run_id=run_id)
        except Exception as exc:
            warning = SourceWarning(source_name, f"Latest-run field health check failed: {exc}")
            all_warnings.append(warning)
            emit(
                "source_field_health_failed",
                warning.message,
                "finalize",
                current_source=source_name,
            )
            continue
        if record.status == "not_applicable":
            continue
        emit(
            "source_field_health_checked",
            record.summary,
            "finalize",
            status="warning" if record.status == "needs_relearn" else "running",
            current_source=source_name,
            counts={
                "jobs_checked": record.job_count,
                "required_missing_field_count": len(record.required_missing_fields),
                "advisory_missing_field_count": len(record.advisory_missing_fields),
                "description_strong_count": record.description_strong_count,
            },
        )
        if record.status == "needs_relearn":
            all_warnings.append(SourceWarning(source_name, record.summary))


def _run_label(options: RunOptions, source_id: str = "") -> str:
    if source_id:
        return f"Single-source run for {source_id}"
    if options.full_source_ingestion:
        return "Full source ingestion"
    return "Daily agent run"


def _max_parallel_sources_from_profile(profile: dict) -> int:
    runtime = profile.get("runtime", {}) if isinstance(profile.get("runtime", {}), dict) else {}
    try:
        configured = int(runtime.get("max_parallel_sources") or 10)
    except (TypeError, ValueError):
        configured = 10
    return max(1, configured)


def _source_access_purpose(options: RunOptions, source_id: str = "") -> str:
    if options.full_source_ingestion:
        return "full_ingest"
    if source_id and options.detail_extraction_limit is None and options.mark_seen and not options.generate_materials:
        return "detail_ingest"
    return "daily_run"


def _max_parallel_ai_matches_from_profile(profile: dict) -> int:
    runtime = profile.get("runtime", {}) if isinstance(profile.get("runtime"), dict) else {}
    try:
        configured = int(runtime.get("max_parallel_ai_matches") or 3)
    except (TypeError, ValueError):
        configured = 3
    return max(1, min(6, configured))


def _evaluate_ai_match(
    root: Path,
    job,
    match,
    profile: dict,
    highlight_reasons: list[str],
    run_id: str,
    stable_id: str,
    llm_model: str,
) -> AiSearchEvaluation:
    return AiSearchService(root).evaluate(
        job,
        match,
        profile,
        highlight_reasons,
        run_id=run_id,
        stable_id=stable_id,
        llm_model=llm_model,
    )


def _prepare_detail_review(
    source_fetch: SourceFetchResult,
    listing_states: list[JobState],
    store: JobStore,
    root: Path,
    source_detail_budget: int | None,
    emit: Callable[..., None],
    pause_every_jobs: int = 0,
    pause_seconds: float = 0.0,
) -> DetailReviewResult:
    if not listing_states:
        return DetailReviewResult(states=[])

    if not _source_follows_detail_pages(source_fetch):
        jobs = [state.job for state in listing_states]
        return DetailReviewResult(states=store.classify(jobs))

    if source_detail_budget is not None and source_detail_budget <= 0:
        return DetailReviewResult(states=[], skipped_for_limit=len(listing_states))

    selected_states = listing_states if source_detail_budget is None else listing_states[:source_detail_budget]
    skipped_for_limit = max(0, len(listing_states) - len(selected_states))
    jobs = [state.job for state in selected_states]
    if not jobs:
        return DetailReviewResult(states=[], skipped_for_limit=skipped_for_limit)

    recipe_path = str(source_fetch.source.get("recipe_path") or "").strip()
    if not recipe_path:
        warning = SourceWarning(source_fetch.source_name, "Recipe source has no recipe_path for detail review.")
        return DetailReviewResult(states=[], warnings=[warning], skipped_for_limit=len(listing_states))

    try:
        recipe = load_project_job_board_recipe(root, recipe_path)
    except (OSError, ValueError) as exc:
        warning = SourceWarning(source_fetch.source_name, f"Detail review could not load recipe: {exc}")
        return DetailReviewResult(states=[], warnings=[warning], skipped_for_limit=len(listing_states))

    session_state_path = None
    if recipe.access.requires_session:
        session_status = SourceSessionService(root).status_for_source(
            str(source_fetch.source.get("source_id") or ""),
            session_scope=recipe.access.session_scope,
        )
        if not session_status.usable:
            warning = SourceWarning(
                source_fetch.source_name,
                (f"Detail review needs a connected source session; current session status is {session_status.label}."),
                str(source_fetch.source.get("url") or ""),
            )
            return DetailReviewResult(states=[], warnings=[warning], skipped_for_limit=len(listing_states))
        session_state_path = resolve_project_path(root, session_status.storage_state_path)

    emit(
        "detail_review_started",
        f"Reviewing {len(jobs)} job(s) in detail for {source_fetch.source_name}.",
        "source_processing",
        current_source=source_fetch.source_name,
        counts={
            "source_index": source_fetch.source_index,
            "source_count": source_fetch.source_count,
            "reviewed_in_detail_count": len(jobs),
            "detail_limit_skipped_count": skipped_for_limit,
        },
    )

    detail_progress = {"requested": 0, "read": 0}
    pause_every_jobs = max(0, int(pause_every_jobs or 0))
    pause_seconds = max(0.0, float(pause_seconds or 0))

    def emit_detail_step(step) -> None:
        phase = str(getattr(step, "phase", "") or "")
        if phase == "Detail page request":
            detail_progress["requested"] = min(len(jobs), detail_progress["requested"] + 1)
        elif phase in {"Detail page read", "Detail page failed"}:
            detail_progress["read"] = min(len(jobs), detail_progress["read"] + 1)
        emit(
            "source_activity",
            f"{step.phase}: {step.detail}",
            "source_ingestion",
            current_source=source_fetch.source_name,
            counts={
                "source_index": source_fetch.source_index,
                "source_count": source_fetch.source_count,
                "activity": 1,
                "detail_read_count": detail_progress["read"],
                "detail_total": len(jobs),
                "detail_fetch_count": detail_progress["requested"],
                "reviewed_in_detail_count": len(jobs),
            },
        )
        if (
            pause_every_jobs
            and pause_seconds
            and phase in {"Detail page read", "Detail page failed"}
            and detail_progress["read"] < len(jobs)
            and detail_progress["read"] % pause_every_jobs == 0
        ):
            emit(
                "detail_review_pause",
                (
                    f"Pausing {int(pause_seconds)}s after {detail_progress['read']} detail page(s) "
                    f"for {source_fetch.source_name}."
                ),
                "source_processing",
                current_source=source_fetch.source_name,
                counts={
                    "source_index": source_fetch.source_index,
                    "source_count": source_fetch.source_count,
                    "detail_read_count": detail_progress["read"],
                    "detail_total": len(jobs),
                    "pause_seconds": int(pause_seconds),
                },
            )
            time.sleep(pause_seconds)

    warnings, attempts = enrich_jobs_with_detail_pages_with_trace(
        jobs,
        recipe,
        detail_page_limit=None,
        session_state_path=session_state_path,
        progress_callback=emit_detail_step,
    )
    enriched = sum(1 for attempt in attempts if attempt.found_fields)
    emit(
        "detail_review_completed",
        (
            f"Reviewed {len(jobs)} job(s) in detail for {source_fetch.source_name}: "
            f"{len(attempts)} detail page(s) opened, {enriched} enriched."
        ),
        "source_processing",
        current_source=source_fetch.source_name,
        counts={
            "source_index": source_fetch.source_index,
            "source_count": source_fetch.source_count,
            "reviewed_in_detail_count": len(jobs),
            "detail_fetch_count": len(attempts),
            "detail_enriched_count": enriched,
            "detail_limit_skipped_count": skipped_for_limit,
        },
    )
    source_warnings = [
        SourceWarning(source_fetch.source_name, warning, source_fetch.source.get("url", "")) for warning in warnings
    ]
    for warning in source_warnings:
        emit(
            "source_warning",
            f"Source warning from {warning.source}: {warning.message}",
            "source_ingestion",
            current_source=source_fetch.source_name,
            counts={
                "source_index": source_fetch.source_index,
                "source_count": source_fetch.source_count,
                "warnings_count": 1,
            },
        )
    return DetailReviewResult(
        states=store.classify(jobs),
        warnings=source_warnings,
        attempts=len(attempts),
        enriched=enriched,
        skipped_for_limit=skipped_for_limit,
    )


def _source_follows_detail_pages(source_fetch: SourceFetchResult) -> bool:
    return bool(source_fetch.result.metadata.get("detail_follow_enabled"))


def _record_totals(record: RunRecord) -> dict[str, int]:
    return {
        "total_loaded": record.total_loaded,
        "new_roles": record.new_roles,
        "changed_roles": record.changed_roles,
        "strong_matches": record.strong_matches,
        "exploratory_matches": record.exploratory_matches,
        "weak_matches": record.weak_matches,
        "excluded_roles": record.excluded_roles,
        "source_warnings": record.source_warnings,
        "generated_job_count": record.generated_job_count,
    }


def _summary_for_record(summary: dict, existing_totals: dict[str, int], generated_job_count: int) -> dict[str, int]:
    values = {
        "total_loaded": summary["total_loaded"],
        "new_roles": summary["new_roles"],
        "changed_roles": summary["changed_roles"],
        "strong_matches": summary["strong_matches"],
        "exploratory_matches": summary["exploratory_matches"],
        "weak_matches": summary["weak_matches"],
        "excluded_roles": summary["excluded_roles"],
        "source_warnings": summary["source_warnings"],
        "generated_job_count": generated_job_count,
    }
    if not existing_totals:
        return values
    return {key: existing_totals.get(key, 0) + value for key, value in values.items()}


def _new_source_counts(source_fetch: SourceFetchResult) -> dict:
    metadata = source_fetch.result.metadata
    pagination_fetch_count = int(metadata.get("pagination_fetch_count") or 0)
    pagination_max_pages = int(metadata.get("pagination_max_pages") or 0)
    visible_total = int(metadata.get("visible_total_job_count") or 0)
    page_explored_count = max(1 if source_fetch.result.jobs or metadata else 0, 1 + pagination_fetch_count)
    page_total = max(page_explored_count, pagination_max_pages)
    observed = int(metadata.get("listing_extracted_count") or len(source_fetch.result.jobs) or 0)
    if visible_total and observed and page_explored_count:
        per_page = max(1, round(observed / page_explored_count))
        page_total = max(page_total, (visible_total + per_page - 1) // per_page)
    return {
        "source_index": source_fetch.source_index,
        "source_count": source_fetch.source_count,
        "jobs_found": len(source_fetch.result.jobs),
        "warnings_count": len(source_fetch.result.warnings),
        "listing_observed_count": int(metadata.get("listing_observed_count") or 0),
        "listing_extracted_count": int(metadata.get("listing_extracted_count") or 0),
        "listing_limit_skipped_count": int(metadata.get("listing_limit_skipped_count") or 0),
        "pagination_fetch_count": pagination_fetch_count,
        "pagination_duplicate_page_count": int(metadata.get("pagination_duplicate_page_count") or 0),
        "pagination_max_pages": pagination_max_pages,
        "visible_total_job_count": visible_total,
        "page_explored_count": page_explored_count,
        "page_total": page_total,
        "detail_fetch_count": int(metadata.get("detail_fetch_count") or 0),
        "detail_enriched_count": int(metadata.get("detail_enriched_count") or 0),
        "detail_limit_skipped_count": 0,
        "reviewed_in_detail_count": 0,
        "new_roles": 0,
        "changed_roles": 0,
        "candidates_processed": 0,
        "strong_matches": 0,
        "exploratory_matches": 0,
        "weak_matches": 0,
        "excluded_roles": 0,
        "included_roles": 0,
        "duplicates_skipped": 0,
        "new_candidates": 0,
        "changed_candidates": 0,
        "previously_seen": 0,
        "previously_seen_skipped": 0,
        "highlighted_matches": 0,
        "ai_evaluations_completed": 0,
        "ai_evaluations_failed": 0,
        "ai_prioritized": 0,
    }


def _source_processed_message(source_fetch: SourceFetchResult, counts: dict) -> str:
    changed_text = counts["new_roles"] + counts["changed_roles"]
    seen_text = (
        f", {counts['previously_seen_skipped']} already seen skipped" if counts.get("previously_seen_skipped") else ""
    )
    detail_text = (
        f", {counts['reviewed_in_detail_count']} reviewed in detail" if counts.get("reviewed_in_detail_count") else ""
    )
    limit_text = (
        f", {counts['detail_limit_skipped_count']} waiting for next run"
        if counts.get("detail_limit_skipped_count")
        else ""
    )
    return (
        f"Processed source {source_fetch.source_index}/{source_fetch.source_count}: {source_fetch.source_name} - "
        f"{counts['jobs_found']} jobs, {changed_text} new/changed{seen_text}{detail_text}{limit_text}, "
        f"{counts['strong_matches']} strong, {counts['exploratory_matches']} exploratory, "
        f"{counts['highlighted_matches']} highlights, {counts['ai_evaluations_completed']} AI summaries"
    )


def _state_status_counts(states: list) -> dict[str, int]:
    return {
        "new": sum(1 for state in states if state.status == "new"),
        "changed": sum(1 for state in states if state.status == "changed"),
        "previously_seen": sum(1 for state in states if state.status == "previously_seen"),
    }


def _match_highlight_message(job_title: str, score: int, reasons: list[str]) -> str:
    return f"Highlighted match: {job_title} - {score}% - {'; '.join(reasons[:4])}"
