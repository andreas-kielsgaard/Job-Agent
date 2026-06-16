from __future__ import annotations

import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

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
    apply_detail_api_record as _apply_detail_api_record,
)
from job_agent.services.recipes.extraction import (
    apply_detail_html as _apply_detail_html,
)
from job_agent.services.recipes.extraction import (
    canonical_url as _canonical_url,
)
from job_agent.services.recipes.extraction import (
    extract_jobs_from_api_payload_with_stats as _extract_jobs_from_api_payload_with_stats,
)
from job_agent.services.recipes.extraction import (
    extract_jobs_with_recipe_with_stats as _extract_jobs_with_recipe_with_stats,
)
from job_agent.services.recipes.extraction import (
    has_detail_selectors as _has_detail_selectors,
)
from job_agent.services.recipes.extraction import (
    json_path as _json_path,
)
from job_agent.services.recipes.extraction import (
    select_text as _select_text,
)
from job_agent.services.recipes.extraction import (
    should_reject as _should_reject,
)
from job_agent.services.recipes.fetching import (
    fetch_json_api as _fetch_json_api,
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
    load_project_job_board_recipe,
)
from job_agent.services.recipes.models import (
    AcceptRecipe,
    AccessRecipe,
    ApiFieldMapping,
    ApiPaginationRecipe,
    ApiRequestRecipe,
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
    "ApiFieldMapping",
    "ApiPaginationRecipe",
    "ApiRequestRecipe",
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
    "extract_jobs_with_recipe_from_api_payload",
    "extract_jobs_with_recipe",
    "extract_jobs_with_recipe_from_html",
    "extract_jobs_with_recipe_from_url",
    "find_pagination_links",
    "job_board_recipe_from_mapping",
    "load_job_board_recipe",
    "load_project_job_board_recipe",
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
    if recipe.listing_api.url:
        return []
    jobs, _stats = _extract_jobs_with_recipe_with_stats(html, base_url, recipe, source_name=source_name)
    return jobs


def extract_jobs_with_recipe_from_html(
    html: str,
    base_url: str,
    recipe: JobBoardRecipe,
    mode_used: str = "local_fixture_html",
    warnings: list[str] | None = None,
) -> RecipeExtractionResult:
    if recipe.listing_api.url:
        result = RecipeExtractionResult(
            jobs=[],
            base_url=base_url,
            mode_used=mode_used,
            warnings=list(warnings or [])
            + ["API-backed recipe requires a JSON API fixture or public URL, not listing HTML."],
            access_strategy="api",
        )
        _attach_recipe_checks(result, recipe)
        return result
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


def extract_jobs_with_recipe_from_api_payload(
    payload: Any,
    base_url: str,
    recipe: JobBoardRecipe,
    mode_used: str = "local_api_fixture",
    warnings: list[str] | None = None,
) -> RecipeExtractionResult:
    jobs, listing_stats, total_count = _extract_jobs_from_api_payload_with_stats(
        payload,
        base_url=base_url,
        recipe=recipe,
    )
    result = RecipeExtractionResult(
        jobs=jobs,
        base_url=base_url,
        mode_used=mode_used,
        warnings=list(warnings or []),
        steps=[
            RecipeRunStep(
                phase="Recipe loaded",
                status="completed",
                detail=f"{recipe.source_name} uses API listing access.",
            ),
            RecipeRunStep(
                phase="API listing read",
                status="completed",
                detail=f"Read supplied JSON fixture with {listing_stats.observed_cards} record(s).",
                capability="listing",
                jobs_found=len(jobs),
            ),
            RecipeRunStep(
                phase="Listing records mapped",
                status="completed" if jobs else "warning",
                detail=f"Mapped {len(jobs)} unique job(s) from supplied API records.",
                capability="listing",
                jobs_found=len(jobs),
            ),
            RecipeRunStep(
                phase="Detail page enrichment",
                status="skipped",
                detail="Supplied API fixture did not fetch job-specific detail pages.",
                capability="detail",
            ),
        ],
        listing_pages=[listing_stats],
        visible_total_job_count=total_count,
        access_strategy="api",
        records_observed_count=listing_stats.observed_cards,
        json_records_extracted_count=len(jobs),
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
    material_log: Any | None = None,
    progress_callback: Callable[[RecipeRunStep], None] | None = None,
) -> RecipeExtractionResult:
    normalized_url = validate_public_url(url)
    if recipe.listing_api.url:
        return _extract_jobs_with_api_recipe_from_url(
            normalized_url,
            recipe,
            timeout_seconds=timeout_seconds,
            detail_page_limit=detail_page_limit,
            detail_success_target=detail_success_target,
            detail_listing_page_sample_target=detail_listing_page_sample_target,
            fetch_pagination=fetch_pagination,
            pagination_page_limit=pagination_page_limit,
            job_limit=job_limit,
            fetch_details=fetch_details,
            use_recipe_card_limit=use_recipe_card_limit,
            material_log=material_log,
            progress_callback=progress_callback,
        )
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
    _record_material_html(
        material_log,
        kind="listing",
        url=normalized_url,
        final_url=final_url,
        html=html,
        mode=mode_used,
        warnings=warnings,
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
                **_material_log_kw(material_log),
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
                **_material_log_kw(material_log),
                progress_callback=progress_callback,
                step_collector=steps,
            )
        else:
            page_warnings, fetched_jobs, fetched_urls, fetched_stats = _fetch_pagination_job_pages(
                pagination_links,
                recipe,
                start_url=final_url,
                use_rendered=use_rendered,
                timeout_seconds=timeout_seconds,
                max_pages=page_limit,
                existing_jobs=jobs,
                job_limit=job_limit,
                use_recipe_card_limit=use_recipe_card_limit,
                session_state_path=session_state_path,
                **_material_log_kw(material_log),
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
            **_material_log_kw(material_log),
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


def _extract_jobs_with_api_recipe_from_url(
    start_url: str,
    recipe: JobBoardRecipe,
    *,
    timeout_seconds: int,
    detail_page_limit: int | None,
    detail_success_target: int | None,
    detail_listing_page_sample_target: int | None,
    fetch_pagination: bool,
    pagination_page_limit: int | None,
    job_limit: int | None,
    fetch_details: bool,
    use_recipe_card_limit: bool,
    material_log: Any | None,
    progress_callback: Callable[[RecipeRunStep], None] | None,
) -> RecipeExtractionResult:
    steps: list[RecipeRunStep] = []
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Recipe loaded",
            status="completed",
            detail=f"{recipe.source_name} uses page-declared API listing access.",
        ),
    )
    _emit_recipe_step(
        progress_callback,
        RecipeRunStep(
            phase="API listing request",
            status="running",
            detail=f"Fetching {recipe.listing_api.url} with {recipe.listing_api.method}.",
            capability="listing",
        ),
    )
    payload, final_url, warnings = _fetch_api_request(recipe.listing_api, timeout_seconds)
    api_request_count = 1
    _record_material_json(
        material_log,
        "api-listing-response-1.json",
        payload,
        kind="api_listing",
        metadata=_api_request_metadata(recipe.listing_api, final_url=final_url),
    )
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="API listing fetched",
            status="completed",
            detail=f"Fetched API listing response from {final_url}.",
            capability="listing",
        ),
    )
    jobs, listing_stats, visible_total_job_count = _extract_jobs_from_api_payload_with_stats(
        payload,
        base_url=start_url,
        recipe=recipe,
        use_recipe_card_limit=use_recipe_card_limit,
    )
    jobs = _limited_jobs(jobs, job_limit)
    if listing_stats.extracted_jobs > len(jobs):
        listing_stats.limit_skipped_count += listing_stats.extracted_jobs - len(jobs)
        listing_stats.extracted_jobs = len(jobs)
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Listing records mapped",
            status="completed" if jobs else "warning",
            detail=f"Mapped {len(jobs)} unique job(s) from {listing_stats.observed_cards} API record(s).",
            capability="listing",
            page_explored_count=1,
            page_total=max(1, recipe.listing_api.pagination.max_pages),
            jobs_found=len(jobs),
        ),
    )

    listing_pages = [listing_stats]
    pagination_fetch_attempts: list[str] = []
    if fetch_pagination and _api_pagination_expected(recipe):
        page_limit = _api_page_limit(recipe, pagination_page_limit)
        page_warnings, jobs, page_attempts, page_stats, extra_requests = _fetch_api_pagination_pages(
            recipe,
            start_url=start_url,
            timeout_seconds=timeout_seconds,
            max_pages=page_limit,
            existing_jobs=jobs,
            job_limit=job_limit,
            use_recipe_card_limit=use_recipe_card_limit,
            material_log=material_log,
            progress_callback=progress_callback,
            step_collector=steps,
        )
        warnings.extend(page_warnings)
        pagination_fetch_attempts.extend(page_attempts)
        listing_pages.extend(page_stats)
        api_request_count += extra_requests
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Pagination detection",
            status="completed" if _api_pagination_expected(recipe) else "skipped",
            detail=(
                f"API pagination strategy {recipe.listing_api.pagination.strategy}; "
                f"fetched {len(pagination_fetch_attempts)} additional API page(s)."
                if _api_pagination_expected(recipe)
                else "API listing recipe does not configure pagination."
            ),
            capability="pagination",
        ),
    )

    detail_attempts: list[DetailPageAttempt] = []
    detail_limit = detail_page_limit
    if fetch_details and recipe.detail_api.url:
        detail_warnings, api_detail_attempts, api_detail_requests = _enrich_jobs_with_detail_api_with_trace(
            jobs,
            recipe,
            timeout_seconds=timeout_seconds,
            detail_page_limit=detail_limit,
            detail_success_target=detail_success_target,
            material_log=material_log,
            progress_callback=progress_callback,
            step_collector=steps,
        )
        warnings.extend(detail_warnings)
        detail_attempts.extend(api_detail_attempts)
        api_request_count += api_detail_requests
    if fetch_details and recipe.detail.follow:
        html_warnings, html_attempts = _enrich_jobs_with_detail_pages_with_trace(
            jobs,
            recipe,
            timeout_seconds=timeout_seconds,
            detail_page_limit=detail_limit,
            detail_success_target=detail_success_target,
            detail_listing_page_sample_target=detail_listing_page_sample_target,
            session_state_path=None,
            material_log=material_log,
            progress_callback=progress_callback,
            step_collector=steps,
        )
        warnings.extend(html_warnings)
        detail_attempts.extend(html_attempts)

    detail_enriched_count = sum(1 for attempt in detail_attempts if attempt.found_fields)
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Detail page enrichment",
            status=(
                "completed"
                if detail_enriched_count
                else "skipped"
                if not fetch_details or not (recipe.detail_api.url or recipe.detail.follow)
                else "warning"
            ),
            detail=_api_detail_trace_detail(recipe, detail_limit, detail_attempts, fetch_details),
            capability="detail",
        ),
    )

    result = RecipeExtractionResult(
        jobs=jobs,
        base_url=start_url,
        mode_used="api_json",
        warnings=warnings,
        steps=steps,
        detail_attempts=detail_attempts,
        detail_fetch_limit=detail_limit,
        detail_fetch_count=len(detail_attempts),
        detail_enriched_count=detail_enriched_count,
        pagination_fetch_count=len(pagination_fetch_attempts),
        pagination_fetch_attempts=pagination_fetch_attempts,
        pagination_strategy_used=f"api_{recipe.listing_api.pagination.strategy}",
        visible_total_job_count=visible_total_job_count,
        listing_pages=listing_pages,
        access_strategy="api",
        api_request_count=api_request_count,
        records_observed_count=sum(page.observed_cards for page in listing_pages),
        json_records_extracted_count=sum(page.extracted_jobs for page in listing_pages),
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


def _fetch_api_pagination_pages(
    recipe: JobBoardRecipe,
    *,
    start_url: str,
    timeout_seconds: int,
    max_pages: int,
    existing_jobs: list[Job],
    job_limit: int | None,
    use_recipe_card_limit: bool,
    material_log: Any | None,
    progress_callback: Callable[[RecipeRunStep], None] | None,
    step_collector: list[RecipeRunStep] | None,
) -> tuple[list[str], list[Job], list[str], list[ListingExtractionStats], int]:
    warnings: list[str] = []
    jobs = list(existing_jobs)
    attempts: list[str] = []
    page_stats: list[ListingExtractionStats] = []
    request_count = 0
    seen_urls = {job.url for job in jobs if job.url}
    for page_index in range(1, max(1, max_pages)):
        if job_limit and len(jobs) >= job_limit:
            break
        if page_index and recipe.listing_api.pagination.request_delay_seconds:
            time.sleep(recipe.listing_api.pagination.request_delay_seconds)
        request = _api_request_for_page(recipe.listing_api, page_index)
        _emit_recipe_step(
            progress_callback,
            RecipeRunStep(
                phase="API pagination request",
                status="running",
                detail=f"Fetching API page {page_index + 1} from {request['url']}.",
                capability="pagination",
            ),
        )
        try:
            payload, final_url, fetch_warnings = _fetch_api_request_parts(request, timeout_seconds)
        except ValueError as exc:
            warnings.append(f"API pagination fetch failed for page {page_index + 1}: {exc}")
            _record_recipe_step(
                step_collector,
                progress_callback,
                RecipeRunStep(
                    phase="API pagination failed",
                    status="failed",
                    detail=f"Could not fetch API page {page_index + 1}: {exc}",
                    capability="pagination",
                ),
            )
            break
        request_count += 1
        warnings.extend(fetch_warnings)
        attempts.append(final_url)
        _record_material_json(
            material_log,
            f"api-listing-response-{page_index + 1}.json",
            payload,
            kind="api_pagination",
            metadata={**_api_request_metadata(recipe.listing_api, final_url=final_url), "page_index": page_index + 1},
        )
        page_jobs, stats, _total_count = _extract_jobs_from_api_payload_with_stats(
            payload,
            base_url=start_url,
            recipe=recipe,
            use_recipe_card_limit=use_recipe_card_limit,
        )
        retained: list[Job] = []
        for job in page_jobs:
            if job.url in seen_urls:
                stats.duplicate_count += 1
                continue
            if job_limit and len(jobs) >= job_limit:
                stats.limit_skipped_count += 1
                continue
            seen_urls.add(job.url)
            retained.append(job)
            jobs.append(job)
        stats.extracted_jobs = len(retained)
        page_stats.append(stats)
        _record_recipe_step(
            step_collector,
            progress_callback,
            RecipeRunStep(
                phase="API pagination read",
                status="completed" if retained else "warning",
                detail=(
                    f"API page {page_index + 1} returned {stats.observed_cards} record(s); "
                    f"{len(retained)} new job(s) retained."
                ),
                capability="pagination",
                page_explored_count=page_index + 1,
                page_total=max_pages,
                jobs_found=len(jobs),
            ),
        )
        if not stats.observed_cards:
            break
    return warnings, jobs, attempts, page_stats, request_count


def _enrich_jobs_with_detail_api_with_trace(
    jobs: list[Job],
    recipe: JobBoardRecipe,
    *,
    timeout_seconds: int,
    detail_page_limit: int | None,
    detail_success_target: int | None,
    material_log: Any | None,
    progress_callback: Callable[[RecipeRunStep], None] | None,
    step_collector: list[RecipeRunStep] | None,
) -> tuple[list[str], list[DetailPageAttempt], int]:
    warnings: list[str] = []
    attempts: list[DetailPageAttempt] = []
    request_count = 0
    target_jobs = jobs[:detail_page_limit] if detail_page_limit is not None else jobs
    enriched_count = 0
    for index, job in enumerate(target_jobs):
        context = _job_context(job)
        _emit_recipe_step(
            progress_callback,
            RecipeRunStep(
                phase="Detail API request",
                status="running",
                detail=f"Fetching detail API for {job.title}.",
                capability="detail",
            ),
        )
        try:
            payload, final_url, fetch_warnings = _fetch_api_request(
                recipe.detail_api,
                timeout_seconds,
                context=context,
            )
        except ValueError as exc:
            warnings.append(f"Detail API fetch failed for {job.url}: {exc}")
            attempts.append(
                DetailPageAttempt(
                    url=job.url,
                    status="failed",
                    detail=str(exc),
                )
            )
            _record_recipe_step(
                step_collector,
                progress_callback,
                RecipeRunStep(
                    phase="Detail API failed",
                    status="failed",
                    detail=f"Could not fetch detail API for {job.title}: {exc}",
                    capability="detail",
                ),
            )
            continue
        request_count += 1
        warnings.extend(fetch_warnings)
        _record_material_json(
            material_log,
            f"api-detail-response-{index + 1}.json",
            payload,
            kind="api_detail",
            metadata={**_api_request_metadata(recipe.detail_api, final_url=final_url), "job_url": job.url},
        )
        detail_record = (
            _json_path(payload, recipe.detail_api.results_path) if recipe.detail_api.results_path else payload
        )
        if isinstance(detail_record, list):
            detail_record = detail_record[0] if detail_record else {}
        found_values = _apply_detail_api_record(job, detail_record, recipe)
        found_fields = sorted(found_values)
        if found_fields:
            enriched_count += 1
        attempts.append(
            DetailPageAttempt(
                url=job.url,
                status="completed" if found_fields else "empty",
                found_fields=found_fields,
                detail=(
                    f"Detail API found fields: {', '.join(found_fields)}."
                    if found_fields
                    else "Detail API returned no configured fields."
                ),
            )
        )
        _record_recipe_step(
            step_collector,
            progress_callback,
            RecipeRunStep(
                phase="Detail API read",
                status="completed" if found_fields else "warning",
                detail=(
                    f"Detail API for {job.title} found fields: {', '.join(found_fields)}."
                    if found_fields
                    else f"Detail API for {job.title} returned no configured fields."
                ),
                capability="detail",
            ),
        )
        if detail_success_target and enriched_count >= detail_success_target:
            break
    return warnings, attempts, request_count


def _api_pagination_expected(recipe: JobBoardRecipe) -> bool:
    return bool(recipe.listing_api.url and recipe.listing_api.pagination.strategy != "none")


def _api_page_limit(recipe: JobBoardRecipe, override: int | None) -> int:
    if override and override > 0:
        return max(1, override)
    return max(1, recipe.listing_api.pagination.max_pages)


def _api_request_for_page(api, page_index: int) -> dict[str, Any]:
    params = deepcopy(api.params)
    body = deepcopy(api.body)
    pagination = api.pagination
    page_size = (
        pagination.page_size
        or _int_from_mapping(body, pagination.page_size_param)
        or _int_from_mapping(params, pagination.page_size_param)
    )
    if pagination.strategy in {"page", "offset"} and pagination.page_param:
        _set_mapping_value(params, body, pagination.page_param, pagination.page_start + page_index)
    if pagination.strategy == "offset":
        step = page_size or 1
        _set_mapping_value(params, body, pagination.offset_param, pagination.offset_start + page_index * step)
    if pagination.page_size_param and page_size:
        _set_mapping_value(params, body, pagination.page_size_param, page_size)
    return {
        "method": api.method,
        "url": api.url,
        "headers": api.headers,
        "params": params,
        "body": body,
    }


def _set_mapping_value(params: dict[str, Any], body: dict[str, Any], key: str, value: Any) -> None:
    if not key:
        return
    if key in body or body:
        body[key] = value
    else:
        params[key] = value


def _int_from_mapping(mapping: dict[str, Any], key: str) -> int:
    if not key:
        return 0
    try:
        return int(mapping.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _fetch_api_request(api, timeout_seconds: int, *, context: dict[str, Any] | None = None):
    return _fetch_api_request_parts(
        {
            "method": api.method,
            "url": api.url,
            "headers": api.headers,
            "params": api.params,
            "body": api.body,
            "context": context,
        },
        timeout_seconds,
    )


def _fetch_api_request_parts(request: dict[str, Any], timeout_seconds: int):
    return _fetch_json_api(
        method=str(request.get("method") or "GET"),
        url=str(request.get("url") or ""),
        timeout_seconds=timeout_seconds,
        headers=dict(request.get("headers") or {}),
        params=dict(request.get("params") or {}),
        body=dict(request.get("body") or {}),
        context=request.get("context"),
    )


def _job_context(job: Job) -> dict[str, Any]:
    return {
        "title": job.title,
        "url": job.url,
        "application_url": job.application_url,
        "company": job.company,
        "location": job.location,
        "remote": job.remote,
        "rate": job.rate,
        "workload": job.workload,
        "posted_date": job.posted_date,
        "start_date": job.start_date,
    }


def _api_request_metadata(api, *, final_url: str) -> dict[str, Any]:
    return {
        "method": api.method,
        "url": api.url,
        "final_url": final_url,
        "headers": api.headers,
        "params": api.params,
        "body": api.body,
    }


def _api_detail_trace_detail(
    recipe: JobBoardRecipe,
    detail_limit: int | None,
    attempts: list[DetailPageAttempt],
    fetch_details: bool,
) -> str:
    if not fetch_details:
        return "Listing-only run skipped job-specific detail access."
    if not (recipe.detail_api.url or recipe.detail.follow):
        return "Recipe does not configure API or HTML detail access."
    limit_text = "all retained jobs" if detail_limit is None else f"up to {detail_limit} job(s)"
    if not attempts:
        return f"Recipe was allowed to fetch {limit_text}, but no detail access was attempted."
    enriched = sum(1 for attempt in attempts if attempt.found_fields)
    return f"Attempted {len(attempts)} detail access step(s), allowed {limit_text}; {enriched} yielded fields."


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
    material_log: Any | None = None,
    progress_callback: Callable[[RecipeRunStep], None] | None = None,
) -> tuple[list[str], list[DetailPageAttempt]]:
    return _enrich_jobs_with_detail_pages_with_trace(
        jobs,
        recipe,
        timeout_seconds=timeout_seconds,
        detail_page_limit=detail_page_limit,
        detail_success_target=detail_success_target,
        session_state_path=session_state_path,
        material_log=material_log,
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
    material_log: Any | None = None,
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

        _record_material_html(
            material_log,
            kind="detail",
            url=job.url,
            final_url=str(getattr(response, "url", "") or job.url),
            html=response.text,
            mode="static_html",
        )
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
    start_url: str = "",
    use_rendered: bool,
    timeout_seconds: int,
    max_pages: int | None,
    existing_jobs: list[Job],
    job_limit: int | None,
    use_recipe_card_limit: bool,
    session_state_path: str | Path | None = None,
    material_log: Any | None = None,
    progress_callback: Callable[[RecipeRunStep], None] | None = None,
    step_collector: list[RecipeRunStep] | None = None,
) -> tuple[list[str], list[Job], list[str], list[ListingExtractionStats]]:
    return _recipe_fetch_pagination_job_pages(
        pagination_links,
        recipe,
        start_url=start_url,
        use_rendered=use_rendered,
        timeout_seconds=timeout_seconds,
        max_pages=max_pages,
        existing_jobs=existing_jobs,
        job_limit=job_limit,
        use_recipe_card_limit=use_recipe_card_limit,
        fetch_html_for_mode=_fetch_html_for_mode,
        session_state_path=session_state_path,
        material_log=material_log,
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
    material_log: Any | None = None,
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
        material_log=material_log,
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
    material_log: Any | None = None,
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
        material_log=material_log,
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


def _record_material_html(material_log: Any, **kwargs: Any) -> None:
    if not material_log:
        return
    recorder = getattr(material_log, "record_html", None)
    if callable(recorder):
        recorder(**kwargs)


def _record_material_json(
    material_log: Any,
    filename: str,
    payload: Any,
    *,
    kind: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not material_log:
        return
    recorder = getattr(material_log, "record_json", None)
    if callable(recorder):
        recorder(filename, payload, kind=kind, metadata=metadata)


def _material_log_kw(material_log: Any | None) -> dict[str, Any]:
    return {"material_log": material_log} if material_log else {}


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
        return _fetch_rendered_html(url, timeout_seconds) if use_rendered else _fetch_static_html(url, timeout_seconds)
    if use_rendered:
        return _fetch_rendered_html(url, timeout_seconds, session_state_path=session_state_path)
    return _fetch_static_html(url, timeout_seconds, session_state_path=session_state_path)
