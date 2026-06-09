from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from job_agent.models import Job
from job_agent.services.extraction_quality import ExtractionQuality, candidate_quality
from job_agent.services.job_board_check_service import validate_public_url
from job_agent.services.recipes.checks import (
    attach_listing_counts as _attach_listing_counts,
)
from job_agent.services.recipes.checks import (
    attach_recipe_checks as _attach_recipe_checks,
)
from job_agent.services.recipes.checks import (
    expected_detail_fields as _expected_detail_fields,
)
from job_agent.services.recipes.discovery import (
    _login_gate_detected,
    discover_application_entries,
    discover_interactive_pagination_controls,
    discover_pagination_links,
    discover_visible_total_job_count,
    find_pagination_links,
)
from job_agent.services.recipes.discovery import (
    _pagination_urls_to_fetch as _pagination_urls_to_fetch,
)
from job_agent.services.recipes.extraction import (
    apply_detail_html as _apply_detail_html,
)
from job_agent.services.recipes.extraction import (
    canonical_url as _canonical_url,
)
from job_agent.services.recipes.extraction import (
    extract_jobs_with_recipe_with_stats as _extract_jobs_with_recipe_with_stats,
)
from job_agent.services.recipes.extraction import (
    has_detail_selectors as _has_detail_selectors,
)
from job_agent.services.recipes.extraction import (
    select_text as _select_text,
)
from job_agent.services.recipes.extraction import (
    should_reject as _should_reject,
)
from job_agent.services.recipes.fetching import (
    fetch_rendered_html as _fetch_rendered_html,
)
from job_agent.services.recipes.fetching import (
    fetch_static_html as _fetch_static_html,
)
from job_agent.services.recipes.fetching import (
    requests_get_with_session_state as _requests_get_with_session_state,
)
from job_agent.services.recipes.mapping import (
    job_board_recipe_from_mapping,
    load_job_board_recipe,
)
from job_agent.services.recipes.models import (
    AcceptRecipe,
    AccessRecipe,
    ApplicationEntry,
    DetailPageAttempt,
    DetailRecipe,
    JobBoardRecipe,
    LimitRecipe,
    ListingExtractionStats,
    ListingRecipe,
    PaginationLink,
    PaginationRecipe,
    PatternsRecipe,
    RecipeCapabilityCheck,
    RecipeExtractionResult,
    RecipeFieldCheck,
    RecipeRunStep,
    RejectRecipe,
    SelectorValue,
)
from job_agent.services.recipes.pagination import (
    fetch_ajax_pagination_job_pages as _recipe_fetch_ajax_pagination_job_pages,
)
from job_agent.services.recipes.pagination import (
    fetch_browser_click_pagination_job_pages as _recipe_fetch_browser_click_pagination_job_pages,
)
from job_agent.services.recipes.pagination import (
    fetch_pagination_job_pages as _recipe_fetch_pagination_job_pages,
)
from job_agent.services.recipes.pagination import (
    pagination_step_status as _pagination_step_status,
)
from job_agent.services.recipes.pagination import (
    pagination_trace_detail as _pagination_trace_detail,
)

__all__ = [
    "AcceptRecipe",
    "AccessRecipe",
    "ApplicationEntry",
    "DetailPageAttempt",
    "DetailRecipe",
    "JobBoardRecipe",
    "LimitRecipe",
    "ListingExtractionStats",
    "ListingRecipe",
    "PaginationLink",
    "PaginationRecipe",
    "PatternsRecipe",
    "RecipeCapabilityCheck",
    "RecipeExtractionResult",
    "RecipeFieldCheck",
    "RecipeRunStep",
    "RejectRecipe",
    "SelectorValue",
    "check_recipe_against_html",
    "discover_visible_total_job_count",
    "enrich_jobs_with_detail_pages",
    "enrich_jobs_with_detail_pages_with_trace",
    "extract_job_detail_from_html",
    "extract_jobs_with_recipe",
    "extract_jobs_with_recipe_from_html",
    "extract_jobs_with_recipe_from_url",
    "find_pagination_links",
    "job_board_recipe_from_mapping",
    "load_job_board_recipe",
    "quality_from_recipe_result",
]


def _record_recipe_step(
    steps: list[RecipeRunStep] | None,
    progress_callback: Callable[[RecipeRunStep], None] | None,
    step: RecipeRunStep,
) -> None:
    if steps is not None:
        steps.append(step)
    _emit_recipe_step(progress_callback, step)


def _emit_recipe_step(
    progress_callback: Callable[[RecipeRunStep], None] | None,
    step: RecipeRunStep,
) -> None:
    if progress_callback:
        progress_callback(step)


def extract_jobs_with_recipe(
    html: str,
    base_url: str,
    recipe: JobBoardRecipe,
    source_name: str = "",
) -> list[Job]:
    jobs, _stats = _extract_jobs_with_recipe_with_stats(html, base_url, recipe, source_name=source_name)
    return jobs


def extract_jobs_with_recipe_from_html(
    html: str,
    base_url: str,
    recipe: JobBoardRecipe,
    mode_used: str = "local_fixture_html",
    warnings: list[str] | None = None,
) -> RecipeExtractionResult:
    jobs, listing_stats = _extract_jobs_with_recipe_with_stats(html, base_url=base_url, recipe=recipe)
    pagination_links = find_pagination_links(html, base_url, recipe)
    interactive_controls = discover_interactive_pagination_controls(html)
    visible_total_job_count = discover_visible_total_job_count(html)
    result = RecipeExtractionResult(
        jobs=jobs,
        base_url=base_url,
        mode_used=mode_used,
        warnings=list(warnings or []),
        pagination_links=pagination_links,
        observed_pagination_links=discover_pagination_links(html, base_url),
        interactive_pagination_control_count=len(interactive_controls),
        application_entries=discover_application_entries(html, base_url),
        steps=[
            RecipeRunStep(
                phase="Recipe loaded",
                status="completed",
                detail=f"{recipe.source_name} uses {recipe.mode}; listing selector {recipe.listing.card_selector}.",
            ),
            RecipeRunStep(
                phase="Listing page read",
                status="completed",
                detail=f"Read supplied HTML with base URL {base_url}.",
                capability="listing",
            ),
            RecipeRunStep(
                phase="Listing selectors applied",
                status="completed" if jobs else "warning",
                detail=f"Extracted {len(jobs)} unique job(s) from the supplied listing HTML.",
                capability="listing",
            ),
            RecipeRunStep(
                phase="Pagination detection",
                status=_pagination_step_status(recipe, pagination_links, [], len(interactive_controls)),
                detail=_pagination_trace_detail(
                    recipe,
                    pagination_links,
                    discover_pagination_links(html, base_url),
                    [],
                    len(interactive_controls),
                ),
                capability="pagination",
            ),
            RecipeRunStep(
                phase="Detail page enrichment",
                status="skipped",
                detail="Supplied listing HTML did not fetch job-specific detail pages.",
                capability="detail",
            ),
        ],
        listing_pages=[listing_stats],
        pagination_strategy_used=recipe.pagination.strategy,
        visible_total_job_count=visible_total_job_count,
        source_access_login_gate_detected=_login_gate_detected(html),
    )
    _attach_listing_counts(result)
    _attach_recipe_checks(result, recipe)
    return result


def extract_jobs_with_recipe_from_url(
    url: str,
    recipe: JobBoardRecipe,
    rendered: bool | None = None,
    timeout_seconds: int = 15,
    *,
    use_recipe_detail_limit: bool = True,
    detail_page_limit: int | None = None,
    detail_success_target: int | None = None,
    detail_listing_page_sample_target: int | None = None,
    fetch_pagination: bool = False,
    pagination_page_limit: int | None = None,
    job_limit: int | None = None,
    fetch_details: bool = True,
    use_recipe_card_limit: bool = True,
    session_state_path: str | Path | None = None,
    progress_callback: Callable[[RecipeRunStep], None] | None = None,
) -> RecipeExtractionResult:
    normalized_url = validate_public_url(url)
    use_rendered = recipe.mode == "rendered_html" if rendered is None else rendered
    mode_used = "rendered_html" if use_rendered else "static_html"
    steps: list[RecipeRunStep] = []
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Recipe loaded",
            status="completed",
            detail=f"{recipe.source_name} uses {recipe.mode}; listing selector {recipe.listing.card_selector}.",
        ),
    )
    _emit_recipe_step(
        progress_callback,
        RecipeRunStep(
            phase="Listing page request",
            status="running",
            detail=f"Fetching {normalized_url} with {mode_used}.",
            capability="listing",
        ),
    )
    html, final_url, warnings = _fetch_html_for_mode(
        normalized_url,
        timeout_seconds,
        use_rendered=use_rendered,
        session_state_path=session_state_path,
    )
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Listing page fetched",
            status="completed",
            detail=f"Fetched {final_url} with {mode_used}.",
            capability="listing",
        ),
    )
    jobs, listing_stats = _extract_jobs_with_recipe_with_stats(
        html,
        base_url=final_url,
        recipe=recipe,
        use_recipe_card_limit=use_recipe_card_limit,
    )
    jobs = _limited_jobs(jobs, job_limit)
    if listing_stats.extracted_jobs > len(jobs):
        listing_stats.limit_skipped_count += listing_stats.extracted_jobs - len(jobs)
        listing_stats.extracted_jobs = len(jobs)
    pagination_links = find_pagination_links(html, final_url, recipe)
    observed_pagination_links = discover_pagination_links(html, final_url)
    interactive_controls = discover_interactive_pagination_controls(html)
    visible_total_job_count = discover_visible_total_job_count(html)
    application_entries = discover_application_entries(html, final_url)
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Listing selectors applied",
            status="completed" if jobs else "warning",
            detail=f"Extracted {len(jobs)} unique job(s) from the first listing page.",
            capability="listing",
            page_explored_count=1,
            page_total=max(1, len(pagination_links) + 1),
            jobs_found=len(jobs),
        ),
    )

    pagination_fetch_attempts: list[str] = []
    pagination_listing_stats: list[ListingExtractionStats] = []
    if fetch_pagination:
        page_limit = (
            None
            if pagination_page_limit == 0
            else (pagination_page_limit if pagination_page_limit is not None else recipe.pagination.max_pages)
        )
        if recipe.pagination.strategy == "ajax":
            page_warnings, fetched_jobs, fetched_urls, fetched_stats = _fetch_ajax_pagination_job_pages(
                final_url,
                recipe,
                use_rendered=use_rendered,
                timeout_seconds=timeout_seconds,
                max_pages=page_limit,
                existing_jobs=jobs,
                job_limit=job_limit,
                use_recipe_card_limit=use_recipe_card_limit,
                session_state_path=session_state_path,
                progress_callback=progress_callback,
                step_collector=steps,
            )
        elif recipe.pagination.strategy == "browser_click":
            page_warnings, fetched_jobs, fetched_urls, fetched_stats = _fetch_browser_click_pagination_job_pages(
                final_url,
                recipe,
                timeout_seconds=timeout_seconds,
                max_pages=page_limit,
                existing_jobs=jobs,
                job_limit=job_limit,
                use_recipe_card_limit=use_recipe_card_limit,
                session_state_path=session_state_path,
                progress_callback=progress_callback,
                step_collector=steps,
            )
        else:
            page_warnings, fetched_jobs, fetched_urls, fetched_stats = _fetch_pagination_job_pages(
                pagination_links,
                recipe,
                use_rendered=use_rendered,
                timeout_seconds=timeout_seconds,
                max_pages=page_limit,
                existing_jobs=jobs,
                job_limit=job_limit,
                use_recipe_card_limit=use_recipe_card_limit,
                session_state_path=session_state_path,
                progress_callback=progress_callback,
                step_collector=steps,
            )
        warnings.extend(page_warnings)
        pagination_fetch_attempts.extend(fetched_urls)
        pagination_listing_stats.extend(fetched_stats)
        jobs = fetched_jobs
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Pagination detection",
            status=_pagination_step_status(
                recipe, pagination_links, pagination_fetch_attempts, len(interactive_controls)
            ),
            detail=_pagination_trace_detail(
                recipe,
                pagination_links,
                observed_pagination_links,
                pagination_fetch_attempts,
                len(interactive_controls),
            ),
            capability="pagination",
        ),
    )

    detail_limit = recipe.detail.max_detail_pages if use_recipe_detail_limit else detail_page_limit
    listing_pages_for_detail = [listing_stats, *pagination_listing_stats]
    detail_target_jobs, detail_job_listing_page_indices, detail_effective_page_target = (
        _detail_sample_jobs_by_listing_page(
            jobs,
            listing_pages_for_detail,
            recipe,
            detail_listing_page_sample_target,
            detail_limit,
        )
    )
    if fetch_details:
        detail_warnings, detail_attempts = _enrich_jobs_with_detail_pages_with_trace(
            jobs,
            recipe,
            timeout_seconds=timeout_seconds,
            detail_page_limit=detail_limit,
            detail_success_target=detail_success_target,
            detail_listing_page_sample_target=detail_effective_page_target,
            detail_target_jobs=detail_target_jobs,
            detail_job_listing_page_indices=detail_job_listing_page_indices,
            session_state_path=session_state_path,
            progress_callback=progress_callback,
            step_collector=steps,
        )
    else:
        detail_limit = 0
        detail_warnings = []
        detail_attempts = []
    warnings.extend(detail_warnings)
    detail_enriched_count = sum(1 for attempt in detail_attempts if attempt.found_fields)
    detail_verified_listing_page_count = _verified_detail_listing_page_count(
        detail_attempts,
        detail_job_listing_page_indices,
    )
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Detail page enrichment",
            status="completed"
            if detail_enriched_count
            else "skipped"
            if not fetch_details or not recipe.detail.follow
            else "warning",
            detail=(
                "Listing-only run skipped job-specific detail pages."
                if not fetch_details
                else _detail_trace_detail(recipe, detail_limit, detail_attempts)
            ),
            capability="detail",
        ),
    )

    result = RecipeExtractionResult(
        jobs=jobs,
        base_url=final_url,
        mode_used=mode_used,
        warnings=warnings,
        pagination_links=pagination_links,
        observed_pagination_links=observed_pagination_links,
        application_entries=application_entries,
        detail_attempts=detail_attempts,
        steps=steps,
        detail_fetch_limit=detail_limit,
        detail_fetch_count=len(detail_attempts),
        detail_enriched_count=detail_enriched_count,
        detail_listing_page_sample_target=detail_effective_page_target,
        detail_verified_listing_page_count=detail_verified_listing_page_count,
        pagination_fetch_count=len(pagination_fetch_attempts),
        pagination_fetch_attempts=pagination_fetch_attempts,
        source_access_session_used=bool(session_state_path),
        source_access_login_gate_detected=_login_gate_detected(html),
        pagination_strategy_used=recipe.pagination.strategy,
        interactive_pagination_control_count=len(interactive_controls),
        visible_total_job_count=visible_total_job_count,
        listing_pages=[listing_stats, *pagination_listing_stats],
    )
    _attach_listing_counts(result)
    _attach_recipe_checks(result, recipe)
    return result


def enrich_jobs_with_detail_pages(
    jobs: list[Job],
    recipe: JobBoardRecipe,
    timeout_seconds: int = 15,
) -> list[str]:
    warnings, _attempts = _enrich_jobs_with_detail_pages_with_trace(
        jobs,
        recipe,
        timeout_seconds=timeout_seconds,
        detail_page_limit=recipe.detail.max_detail_pages,
    )
    return warnings


def enrich_jobs_with_detail_pages_with_trace(
    jobs: list[Job],
    recipe: JobBoardRecipe,
    timeout_seconds: int = 15,
    detail_page_limit: int | None = None,
    detail_success_target: int | None = None,
    detail_listing_page_sample_target: int | None = None,
    detail_target_jobs: list[Job] | None = None,
    detail_job_listing_page_indices: dict[str, int] | None = None,
    session_state_path: str | Path | None = None,
    progress_callback: Callable[[RecipeRunStep], None] | None = None,
) -> tuple[list[str], list[DetailPageAttempt]]:
    return _enrich_jobs_with_detail_pages_with_trace(
        jobs,
        recipe,
        timeout_seconds=timeout_seconds,
        detail_page_limit=detail_page_limit,
        detail_success_target=detail_success_target,
        session_state_path=session_state_path,
        progress_callback=progress_callback,
    )


def _enrich_jobs_with_detail_pages_with_trace(
    jobs: list[Job],
    recipe: JobBoardRecipe,
    timeout_seconds: int = 15,
    detail_page_limit: int | None = None,
    detail_success_target: int | None = None,
    detail_listing_page_sample_target: int | None = None,
    detail_target_jobs: list[Job] | None = None,
    detail_job_listing_page_indices: dict[str, int] | None = None,
    session_state_path: str | Path | None = None,
    progress_callback: Callable[[RecipeRunStep], None] | None = None,
    step_collector: list[RecipeRunStep] | None = None,
) -> tuple[list[str], list[DetailPageAttempt]]:
    if not recipe.detail.follow:
        return [], []

    warnings: list[str] = []
    attempts: list[DetailPageAttempt] = []
    if not _has_detail_selectors(recipe):
        return ["detail.follow is true, but no detail selectors are configured."], attempts

    expected_fields = _expected_detail_fields(recipe)
    target_jobs = detail_target_jobs if detail_target_jobs is not None else jobs
    if detail_page_limit is not None:
        target_jobs = target_jobs[:detail_page_limit]
    enriched_count = 0
    enriched_listing_pages: set[int] = set()
    for index, job in enumerate(target_jobs):
        if _should_reject(job.title, job.url, job.description, recipe):
            continue
        if index and recipe.detail.request_delay_seconds:
            _emit_recipe_step(
                progress_callback,
                RecipeRunStep(
                    phase="Detail request delay",
                    status="running",
                    detail=f"Waiting {recipe.detail.request_delay_seconds:g}s before opening the next detail page.",
                    capability="detail",
                ),
            )
            time.sleep(recipe.detail.request_delay_seconds)
        _emit_recipe_step(
            progress_callback,
            RecipeRunStep(
                phase="Detail page request",
                status="running",
                detail=f"Opening detail page for {job.title}: {job.url}",
                capability="detail",
            ),
        )
        try:
            response = _requests_get_with_session_state(
                job.url,
                timeout_seconds,
                user_agent="Job-Agent recipe detail fetcher (public page; low volume)",
                session_state_path=session_state_path,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            warnings.append(f"Detail fetch failed for {job.url}: {exc}")
            attempts.append(
                DetailPageAttempt(
                    url=job.url,
                    status="failed",
                    missing_fields=expected_fields,
                    detail=str(exc),
                )
            )
            _record_recipe_step(
                step_collector,
                progress_callback,
                RecipeRunStep(
                    phase="Detail page failed",
                    status="failed",
                    detail=f"Could not open {job.url}: {exc}",
                    capability="detail",
                ),
            )
            continue

        found_values = _apply_detail_html(job, response.text, recipe)
        found_fields = sorted(found_values)
        if found_fields:
            enriched_count += 1
            page_index = (detail_job_listing_page_indices or {}).get(job.url)
            if page_index is not None:
                enriched_listing_pages.add(page_index)
        missing_fields = [field for field in expected_fields if field not in found_values]
        if not found_fields:
            warnings.append(f"Detail page fetched for {job.url}, but no configured detail fields were found.")
        attempts.append(
            DetailPageAttempt(
                url=job.url,
                status="completed" if found_fields else "empty",
                found_fields=found_fields,
                missing_fields=missing_fields,
                detail=(
                    f"Found detail fields: {', '.join(found_fields)}."
                    if found_fields
                    else "No configured detail selectors or structured data fields produced values."
                ),
            )
        )
        _record_recipe_step(
            step_collector,
            progress_callback,
            RecipeRunStep(
                phase="Detail page read",
                status="completed" if found_fields else "warning",
                detail=(
                    f"Opened {job.url}; found fields: {', '.join(found_fields)}."
                    if found_fields
                    else f"Opened {job.url}; no configured detail fields produced values."
                ),
                capability="detail",
            ),
        )
        if detail_listing_page_sample_target and len(enriched_listing_pages) >= detail_listing_page_sample_target:
            break
        if not detail_listing_page_sample_target and detail_success_target and enriched_count >= detail_success_target:
            break
    return warnings, attempts


def _detail_sample_jobs_by_listing_page(
    jobs: list[Job],
    listing_pages: list[ListingExtractionStats],
    recipe: JobBoardRecipe,
    listing_page_sample_target: int | None,
    detail_page_limit: int | None,
) -> tuple[list[Job] | None, dict[str, int], int]:
    if not listing_page_sample_target or listing_page_sample_target <= 0:
        return None, {}, 0

    page_groups: list[tuple[int, list[Job]]] = []
    cursor = 0
    for page_index, page in enumerate(listing_pages):
        page_count = max(0, page.extracted_jobs)
        page_jobs = jobs[cursor : cursor + page_count]
        cursor += page_count
        candidates = [
            job for job in page_jobs if job.url and not _should_reject(job.title, job.url, job.description, recipe)
        ]
        if candidates:
            page_groups.append((page_index, candidates))

    if not page_groups:
        return [], {}, 0

    effective_target = min(listing_page_sample_target, len(page_groups))
    max_attempts = (
        detail_page_limit if detail_page_limit is not None else sum(len(page_jobs) for _, page_jobs in page_groups)
    )
    selected: list[Job] = []
    page_indices_by_url: dict[str, int] = {}
    round_index = 0
    while max_attempts is None or len(selected) < max_attempts:
        added_this_round = False
        for page_index, page_jobs in page_groups:
            if round_index >= len(page_jobs):
                continue
            job = page_jobs[round_index]
            if job.url in page_indices_by_url:
                continue
            selected.append(job)
            page_indices_by_url[job.url] = page_index
            added_this_round = True
            if max_attempts is not None and len(selected) >= max_attempts:
                break
        if not added_this_round or (max_attempts is not None and len(selected) >= max_attempts):
            break
        round_index += 1
    return selected, page_indices_by_url, effective_target


def _verified_detail_listing_page_count(
    attempts: list[DetailPageAttempt],
    page_indices_by_url: dict[str, int],
) -> int:
    verified_pages = {
        page_indices_by_url[attempt.url]
        for attempt in attempts
        if attempt.found_fields and attempt.url in page_indices_by_url
    }
    return len(verified_pages)


def extract_job_detail_from_html(
    html: str,
    base_url: str,
    recipe: JobBoardRecipe,
    source_name: str = "",
) -> Job:
    soup = BeautifulSoup(html, "html.parser")
    url = _canonical_url(soup, base_url)
    job = Job(
        title="",
        company="Unknown",
        source=source_name or recipe.source_name,
        url=url,
        application_url=url,
        source_confidence="recipe-detail-sample",
    )
    _apply_detail_html(job, html, recipe)
    if not job.title:
        job.title = _select_text(soup, "h1") or _select_text(soup, "title")
    if not job.description:
        job.description = _select_text(soup, "main")[:3000]
        job.raw_text = job.description[:5000]
    if "Detail page sample parsed by recipe; verify details manually." not in job.extraction_notes:
        job.extraction_notes.append("Detail page sample parsed by recipe; verify details manually.")
    return job


def _limited_jobs(jobs: list[Job], job_limit: int | None) -> list[Job]:
    if job_limit is None or job_limit <= 0:
        return jobs
    return jobs[:job_limit]


def _fetch_pagination_job_pages(
    pagination_links: list[PaginationLink],
    recipe: JobBoardRecipe,
    *,
    use_rendered: bool,
    timeout_seconds: int,
    max_pages: int | None,
    existing_jobs: list[Job],
    job_limit: int | None,
    use_recipe_card_limit: bool,
    session_state_path: str | Path | None = None,
    progress_callback: Callable[[RecipeRunStep], None] | None = None,
    step_collector: list[RecipeRunStep] | None = None,
) -> tuple[list[str], list[Job], list[str], list[ListingExtractionStats]]:
    return _recipe_fetch_pagination_job_pages(
        pagination_links,
        recipe,
        use_rendered=use_rendered,
        timeout_seconds=timeout_seconds,
        max_pages=max_pages,
        existing_jobs=existing_jobs,
        job_limit=job_limit,
        use_recipe_card_limit=use_recipe_card_limit,
        fetch_html_for_mode=_fetch_html_for_mode,
        session_state_path=session_state_path,
        progress_callback=progress_callback,
        step_collector=step_collector,
    )


def _fetch_ajax_pagination_job_pages(
    base_url: str,
    recipe: JobBoardRecipe,
    *,
    use_rendered: bool,
    timeout_seconds: int,
    max_pages: int | None,
    existing_jobs: list[Job],
    job_limit: int | None,
    use_recipe_card_limit: bool,
    session_state_path: str | Path | None = None,
    progress_callback: Callable[[RecipeRunStep], None] | None = None,
    step_collector: list[RecipeRunStep] | None = None,
) -> tuple[list[str], list[Job], list[str], list[ListingExtractionStats]]:
    return _recipe_fetch_ajax_pagination_job_pages(
        base_url,
        recipe,
        use_rendered=use_rendered,
        timeout_seconds=timeout_seconds,
        max_pages=max_pages,
        existing_jobs=existing_jobs,
        job_limit=job_limit,
        use_recipe_card_limit=use_recipe_card_limit,
        fetch_html_for_mode=_fetch_html_for_mode,
        session_state_path=session_state_path,
        progress_callback=progress_callback,
        step_collector=step_collector,
    )


def _fetch_browser_click_pagination_job_pages(
    start_url: str,
    recipe: JobBoardRecipe,
    *,
    timeout_seconds: int,
    max_pages: int | None,
    existing_jobs: list[Job],
    job_limit: int | None,
    use_recipe_card_limit: bool,
    session_state_path: str | Path | None = None,
    progress_callback: Callable[[RecipeRunStep], None] | None = None,
    step_collector: list[RecipeRunStep] | None = None,
) -> tuple[list[str], list[Job], list[str], list[ListingExtractionStats]]:
    return _recipe_fetch_browser_click_pagination_job_pages(
        start_url,
        recipe,
        timeout_seconds=timeout_seconds,
        max_pages=max_pages,
        existing_jobs=existing_jobs,
        job_limit=job_limit,
        use_recipe_card_limit=use_recipe_card_limit,
        session_state_path=session_state_path,
        progress_callback=progress_callback,
        step_collector=step_collector,
    )


def _detail_trace_detail(
    recipe: JobBoardRecipe,
    detail_limit: int | None,
    attempts: list[DetailPageAttempt],
) -> str:
    if not recipe.detail.follow:
        return "Recipe detail.follow is false, so no job-specific detail pages were requested."
    limit_text = "all retained jobs" if detail_limit is None else f"up to {detail_limit} job(s)"
    if not attempts:
        return f"Recipe was allowed to fetch {limit_text}, but no detail page was attempted."
    enriched = sum(1 for attempt in attempts if attempt.found_fields)
    return (
        f"Attempted {len(attempts)} detail page(s), allowed {limit_text}; {enriched} yielded configured detail fields."
    )


def check_recipe_against_html(
    html: str,
    base_url: str,
    recipe: JobBoardRecipe,
    follow_detail: bool = False,
) -> ExtractionQuality:
    jobs = extract_jobs_with_recipe(html, base_url=base_url, recipe=recipe)
    quality = ExtractionQuality(label=f"Recipe: {recipe.source_name}")
    if follow_detail:
        quality.warnings.extend(enrich_jobs_with_detail_pages(jobs, recipe))
    quality.candidates = [candidate_quality(job) for job in jobs]
    if not jobs:
        quality.warnings.append("Recipe extraction found no matching job cards.")
    return quality


def quality_from_recipe_result(result: RecipeExtractionResult, recipe: JobBoardRecipe) -> ExtractionQuality:
    quality = ExtractionQuality(label=f"Recipe: {recipe.source_name}")
    quality.final_url = result.base_url
    quality.warnings = list(result.warnings)
    quality.candidates = [candidate_quality(job) for job in result.jobs]
    if not result.jobs:
        quality.warnings.append("Recipe extraction found no matching job cards.")
    return quality


def _fetch_html_for_mode(
    url: str,
    timeout_seconds: int,
    *,
    use_rendered: bool,
    session_state_path: str | Path | None = None,
) -> tuple[str, str, list[str]]:
    if session_state_path is None:
        return (
            _fetch_rendered_html(url, timeout_seconds)
            if use_rendered
            else _fetch_static_html(url, timeout_seconds)
        )
    if use_rendered:
        return _fetch_rendered_html(url, timeout_seconds, session_state_path=session_state_path)
    return _fetch_static_html(url, timeout_seconds, session_state_path=session_state_path)


