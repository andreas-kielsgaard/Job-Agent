from __future__ import annotations

from job_agent.models import Job
from job_agent.services.recipes.mapping import _selectors
from job_agent.services.recipes.models import (
    JobBoardRecipe,
    RecipeCapabilityCheck,
    RecipeExtractionResult,
    RecipeFieldCheck,
)


def attach_recipe_checks(result: RecipeExtractionResult, recipe: JobBoardRecipe) -> None:
    result.field_checks = field_checks(result.jobs, recipe)
    result.capability_checks = capability_checks(result, recipe)


def attach_listing_counts(result: RecipeExtractionResult) -> None:
    result.listing_observed_count = sum(page.observed_cards for page in result.listing_pages)
    result.listing_extracted_count = sum(page.extracted_jobs for page in result.listing_pages)
    result.listing_missing_url_count = sum(page.missing_url_count for page in result.listing_pages)
    result.listing_rejected_count = sum(page.rejected_count for page in result.listing_pages)
    result.listing_duplicate_count = sum(page.duplicate_count for page in result.listing_pages)
    result.listing_limit_skipped_count = sum(page.limit_skipped_count for page in result.listing_pages)
    pagination_pages = result.listing_pages[1:]
    pagination_observed = sum(page.observed_cards for page in pagination_pages)
    pagination_duplicates = sum(page.duplicate_count for page in pagination_pages)
    result.pagination_duplicate_page_count = sum(
        1
        for page in pagination_pages
        if page.observed_cards and page.extracted_jobs == 0 and page.duplicate_count >= page.observed_cards
    )
    result.pagination_duplicate_ratio = (
        round(pagination_duplicates / pagination_observed, 3) if pagination_observed else 0.0
    )
    result.pagination_unique_jobs_from_fetched_pages = sum(page.extracted_jobs for page in pagination_pages)


def pagination_expected(recipe: JobBoardRecipe) -> bool:
    return bool(
        recipe.pagination.strategy in {"ajax", "browser_click"}
        or recipe.pagination.page_link_selector
        or recipe.pagination.next_selector
    )


def expected_detail_fields(recipe: JobBoardRecipe) -> list[str]:
    fields: list[str] = []
    selector_map = {
        "title": recipe.detail.title_selector,
        "description": recipe.detail.description_selector,
        "location": recipe.detail.location_selector,
        "remote": recipe.detail.remote_selector,
        "rate": recipe.detail.rate_selector,
        "workload": recipe.detail.workload_selector,
        "posted_date": recipe.detail.posted_date_selector,
        "start_date": recipe.detail.start_date_selector,
        "languages": recipe.detail.language_selector,
    }
    for field_name, selector in selector_map.items():
        if _selectors(selector):
            fields.append(field_name)
    if recipe.detail.use_json_ld:
        for field_name in ["title", "description", "posted_date", "workload", "location"]:
            if field_name not in fields:
                fields.append(field_name)
    pattern_map = {
        "location": recipe.patterns.location_regex,
        "remote": recipe.patterns.remote_regex,
        "rate": recipe.patterns.rate_regex,
        "workload": recipe.patterns.workload_regex or recipe.patterns.work_type_regex,
        "posted_date": recipe.patterns.posted_date_regex,
        "start_date": recipe.patterns.start_date_regex,
        "languages": recipe.patterns.language_regex,
    }
    for field_name, pattern in pattern_map.items():
        if pattern and field_name not in fields:
            fields.append(field_name)
    return fields


def capability_checks(result: RecipeExtractionResult, recipe: JobBoardRecipe) -> list[RecipeCapabilityCheck]:
    pagination_is_expected = pagination_expected(recipe)
    url_pagination_expected = recipe.pagination.strategy == "url" and bool(
        recipe.pagination.page_link_selector or recipe.pagination.next_selector
    )
    ajax_pagination_expected = recipe.pagination.strategy == "ajax"
    browser_click_pagination_expected = recipe.pagination.strategy == "browser_click"
    interactive_controls_observed = result.interactive_pagination_control_count > 0
    missing_browser_strategy = (
        interactive_controls_observed and not browser_click_pagination_expected and not result.pagination_links
    )
    missing_ajax_template = ajax_pagination_expected and not recipe.pagination.ajax_url_template
    pagination_duplicate_blocked = (
        pagination_is_expected and result.pagination_fetch_count > 0 and result.pagination_duplicate_ratio >= 0.8
    )
    pagination_strategy_failed = bool(
        missing_browser_strategy
        or missing_ajax_template
        or pagination_duplicate_blocked
        or (ajax_pagination_expected and not result.pagination_fetch_count)
        or (browser_click_pagination_expected and not result.pagination_fetch_count)
    )
    visible_total_blocked = _visible_total_access_blocked(result)
    source_access_expected = bool(
        recipe.access.requires_session or pagination_duplicate_blocked or result.source_access_login_gate_detected
    )
    source_access_observed = bool(result.source_access_session_used and not result.source_access_login_gate_detected)
    source_access_failed = source_access_expected and not source_access_observed
    detail_expected = recipe.detail.follow
    detail_page_target_met = (
        not result.detail_listing_page_sample_target
        or result.detail_verified_listing_page_count >= result.detail_listing_page_sample_target
    )
    application_observed = bool(result.application_entries)
    return [
        RecipeCapabilityCheck(
            capability="listing_cards",
            status="pass" if result.jobs else "fail",
            expected=True,
            observed=bool(result.jobs),
            detail=f"{len(result.jobs)} job(s) extracted from configured listing cards.",
        ),
        RecipeCapabilityCheck(
            capability="listing_total_access",
            status=("fail" if visible_total_blocked else "pass" if result.visible_total_job_count else "not_expected"),
            expected=bool(result.visible_total_job_count),
            observed=bool(result.visible_total_job_count and len(result.jobs) >= result.visible_total_job_count),
            detail=_visible_total_access_detail(result),
        ),
        RecipeCapabilityCheck(
            capability="job_urls",
            status="pass" if len({job.url for job in result.jobs if job.url}) else "fail",
            expected=True,
            observed=bool({job.url for job in result.jobs if job.url}),
            detail=f"{len({job.url for job in result.jobs if job.url})} unique detail URL(s) found.",
        ),
        RecipeCapabilityCheck(
            capability="pagination_detection",
            status=(
                "pass"
                if url_pagination_expected and result.pagination_links
                else "pass"
                if browser_click_pagination_expected and interactive_controls_observed
                else "pass"
                if ajax_pagination_expected and recipe.pagination.ajax_url_template
                else "fail"
                if pagination_is_expected
                else "observed"
                if result.observed_pagination_links or interactive_controls_observed
                else "not_expected"
            ),
            expected=pagination_is_expected,
            observed=bool(result.pagination_links or result.observed_pagination_links or interactive_controls_observed),
            detail=(
                f"Recipe strategy is {recipe.pagination.strategy}; found {len(result.pagination_links)} configured "
                f"pagination link(s), observed {len(result.observed_pagination_links)} pagination-looking link(s), "
                f"and observed {result.interactive_pagination_control_count} interactive pagination control(s)."
            ),
        ),
        RecipeCapabilityCheck(
            capability="pagination_strategy",
            status=(
                "fail"
                if pagination_strategy_failed
                else "pass"
                if pagination_is_expected
                else "observed"
                if interactive_controls_observed
                else "not_expected"
            ),
            expected=pagination_is_expected,
            observed=bool(result.pagination_fetch_count or result.pagination_links or interactive_controls_observed),
            detail=_pagination_strategy_detail(
                recipe,
                result,
                missing_browser_strategy=missing_browser_strategy,
                missing_ajax_template=missing_ajax_template,
                pagination_duplicate_blocked=pagination_duplicate_blocked,
            ),
        ),
        RecipeCapabilityCheck(
            capability="pagination_navigation",
            status=(
                "fail"
                if pagination_duplicate_blocked
                else "pass"
                if pagination_is_expected and result.pagination_fetch_count
                else "skipped"
                if pagination_is_expected and result.pagination_links
                else "fail"
                if pagination_is_expected
                else "not_expected"
            ),
            expected=pagination_is_expected,
            observed=bool(result.pagination_fetch_count),
            detail=(
                (
                    f"Fetched {result.pagination_fetch_count} pagination page(s), but they produced only "
                    f"{result.pagination_unique_jobs_from_fetched_pages} new job(s) and "
                    f"{result.pagination_duplicate_ratio:.0%} duplicate listings. "
                    "Later pages may require a logged-in session or client-side pagination."
                )
                if pagination_duplicate_blocked
                else (
                    f"Fetched {result.pagination_fetch_count} pagination page(s) as proof; "
                    f"{result.pagination_unique_jobs_from_fetched_pages} new job(s) came from fetched pages."
                )
                if result.pagination_fetch_count
                else f"{recipe.pagination.strategy} pagination was not proof-fetched in this run."
            ),
        ),
        RecipeCapabilityCheck(
            capability="ajax_pagination",
            status=(
                "fail"
                if ajax_pagination_expected and (missing_ajax_template or not result.pagination_fetch_count)
                else "pass"
                if ajax_pagination_expected and result.pagination_fetch_count and not pagination_duplicate_blocked
                else "not_expected"
            ),
            expected=ajax_pagination_expected,
            observed=bool(ajax_pagination_expected and result.pagination_fetch_count),
            detail=(
                f"Fetched {result.pagination_fetch_count} AJAX pagination page(s); "
                f"{result.pagination_unique_jobs_from_fetched_pages} new job(s) came from fetched pages."
                if ajax_pagination_expected and result.pagination_fetch_count
                else "AJAX pagination requires pagination.ajax_url_template and a proof fetch."
                if ajax_pagination_expected
                else "Recipe does not use AJAX pagination."
            ),
        ),
        RecipeCapabilityCheck(
            capability="browser_click_pagination",
            status=(
                "fail"
                if missing_browser_strategy
                else "fail"
                if browser_click_pagination_expected and not result.pagination_fetch_count
                else "pass"
                if browser_click_pagination_expected
                and result.pagination_fetch_count
                and not pagination_duplicate_blocked
                else "not_expected"
            ),
            expected=browser_click_pagination_expected,
            observed=bool(browser_click_pagination_expected and result.pagination_fetch_count),
            detail=(
                f"Clicked through {result.pagination_fetch_count} browser pagination page(s); "
                f"{result.pagination_unique_jobs_from_fetched_pages} new job(s) came from fetched pages."
                if browser_click_pagination_expected and result.pagination_fetch_count
                else (
                    "Interactive pagination controls were observed, but the recipe does not use browser-click pagination."
                    if missing_browser_strategy
                    else "Browser-click pagination requires a click selector and a proof click."
                )
                if browser_click_pagination_expected or missing_browser_strategy
                else "Recipe does not use browser-click pagination."
            ),
        ),
        RecipeCapabilityCheck(
            capability="pagination_duplicate_pages",
            status=(
                "fail"
                if pagination_duplicate_blocked
                else "pass"
                if pagination_is_expected and result.pagination_fetch_count
                else "not_expected"
                if not pagination_is_expected
                else "skipped"
            ),
            expected=pagination_is_expected,
            observed=bool(result.pagination_duplicate_page_count),
            detail=(
                f"{result.pagination_duplicate_page_count} fetched pagination page(s) returned only duplicate listings; "
                f"duplicate ratio {result.pagination_duplicate_ratio:.0%}."
                if result.pagination_fetch_count
                else "No fetched pagination pages were available for duplicate-page verification."
            ),
        ),
        RecipeCapabilityCheck(
            capability="source_access",
            status="fail" if source_access_failed else "pass" if source_access_expected else "not_expected",
            expected=source_access_expected,
            observed=source_access_observed,
            detail=_source_access_detail(
                recipe,
                pagination_duplicate_blocked,
                result.source_access_session_used,
                result.source_access_login_gate_detected,
            ),
        ),
        RecipeCapabilityCheck(
            capability="detail_navigation",
            status=(
                "pass"
                if detail_expected and result.detail_enriched_count and detail_page_target_met
                else "fail"
                if detail_expected
                else "not_expected"
            ),
            expected=detail_expected,
            observed=bool(result.detail_enriched_count and detail_page_target_met),
            detail=_detail_capability_detail(result)
            if detail_expected
            else "Recipe does not request job detail pages.",
        ),
        RecipeCapabilityCheck(
            capability="application_entry",
            status="observed" if application_observed else "not_expected",
            expected=False,
            observed=application_observed,
            detail=(
                f"Observed {len(result.application_entries)} possible application entrypoint(s); "
                "application form extraction is not yet expected by this recipe."
                if application_observed
                else "No application entrypoint was expected or observed."
            ),
        ),
    ]


def field_checks(jobs: list[Job], recipe: JobBoardRecipe) -> list[RecipeFieldCheck]:
    expected_sources = _expected_report_field_sources(recipe)
    fields = [
        "title",
        "url",
        "company",
        "location",
        "remote",
        "rate",
        "workload",
        "posted_date",
        "start_date",
        "languages",
        "description",
    ]
    checks: list[RecipeFieldCheck] = []
    for field_name in fields:
        expected = field_name in expected_sources
        present_jobs = [job for job in jobs if _job_field_present(job, field_name)]
        present_count = len(present_jobs)
        sample_value = _job_field_value(present_jobs[0], field_name) if present_jobs else ""
        status = (
            "pass"
            if expected and present_count
            else "fail"
            if expected
            else "observed"
            if present_count
            else "not_expected"
        )
        checks.append(
            RecipeFieldCheck(
                field=field_name,
                scope="job_report",
                expected=expected,
                status=status,
                present_count=present_count,
                total_count=len(jobs),
                sample_value=sample_value,
                source=expected_sources.get(field_name, ""),
                detail=(
                    f"Expected via {expected_sources[field_name]}; found {present_count}/{len(jobs)}."
                    if expected
                    else f"Not expected by recipe; found {present_count}/{len(jobs)}."
                ),
            )
        )
    return checks


def _visible_total_access_blocked(result: RecipeExtractionResult) -> bool:
    total = int(result.visible_total_job_count or 0)
    reached = len({job.url for job in result.jobs if job.url}) or len(result.jobs)
    if total < 10 or reached >= total:
        return False
    missing = total - reached
    material_gap = max(3, int(total * 0.2))
    return missing >= material_gap


def _visible_total_access_detail(result: RecipeExtractionResult) -> str:
    total = int(result.visible_total_job_count or 0)
    reached = len({job.url for job in result.jobs if job.url}) or len(result.jobs)
    if not total:
        return "No visible total job count was detected on the listing page."
    if _visible_total_access_blocked(result):
        return (
            f"The listing page appears to advertise {total} posting(s), but the verified extractor reached "
            f"only {reached}. Pagination, source access, or the reading plan may be incomplete."
        )
    return f"The listing page appears to advertise {total} posting(s); the verified extractor reached {reached}."


def _pagination_strategy_detail(
    recipe: JobBoardRecipe,
    result: RecipeExtractionResult,
    *,
    missing_browser_strategy: bool,
    missing_ajax_template: bool,
    pagination_duplicate_blocked: bool,
) -> str:
    if missing_browser_strategy:
        return (
            f"Observed {result.interactive_pagination_control_count} interactive pagination control(s), "
            "but the recipe does not declare browser-click pagination."
        )
    if missing_ajax_template:
        return "Recipe declares AJAX pagination but has no ajax_url_template to proof fetch later pages."
    if pagination_duplicate_blocked:
        return (
            f"Recipe declares {recipe.pagination.strategy} pagination, but proof-fetched pages returned only "
            "duplicate listings. Use a different pagination strategy, such as browser-click or AJAX, or refresh "
            "the source recipe."
        )
    if recipe.pagination.strategy in {"ajax", "browser_click"} and not result.pagination_fetch_count:
        return (
            f"Recipe declares {recipe.pagination.strategy} pagination, but no later page was proof-fetched. "
            "The pagination selector or strategy needs review."
        )
    if pagination_expected(recipe):
        return (
            f"Recipe declares {recipe.pagination.strategy} pagination and proof fetched "
            f"{result.pagination_fetch_count} page(s)."
        )
    if result.interactive_pagination_control_count:
        return (
            f"Observed {result.interactive_pagination_control_count} interactive pagination control(s) "
            "without configured pagination."
        )
    return "Recipe does not expect pagination."


def _detail_capability_detail(result: RecipeExtractionResult) -> str:
    detail = (
        f"Attempted {len(result.detail_attempts)} detail page(s); "
        f"{result.detail_enriched_count} yielded configured detail fields."
    )
    if result.detail_listing_page_sample_target:
        detail += (
            f" Verified details on {result.detail_verified_listing_page_count}/"
            f"{result.detail_listing_page_sample_target} listing page(s)."
        )
    return detail


def _source_access_detail(
    recipe: JobBoardRecipe,
    pagination_duplicate_blocked: bool,
    session_used: bool,
    login_gate_detected: bool = False,
) -> str:
    if login_gate_detected:
        if session_used:
            detail = (
                "A connected source session was used, but the page still showed a sign-in or registration gate. "
                "Reconnect the source session, then rerun the source test."
            )
        else:
            detail = "The page showed a sign-in or registration gate before later listings could be verified."
        if recipe.access.session_scope:
            detail += f" Session scope: {recipe.access.session_scope}."
        return detail
    if recipe.access.requires_session:
        if session_used:
            detail = "Connected source session was used for this verification run."
            if recipe.access.session_scope:
                detail += f" Session scope: {recipe.access.session_scope}."
            if pagination_duplicate_blocked:
                detail += " Pagination still returned duplicate pages, so the pagination strategy needs review."
            return detail
        detail = "Recipe declares that this source requires a connected session."
        if recipe.access.session_scope:
            detail += f" Session scope: {recipe.access.session_scope}."
        if recipe.access.setup_hint:
            detail += f" Setup hint: {recipe.access.setup_hint}"
        return detail
    if pagination_duplicate_blocked:
        return (
            "Verification inferred that later listing pages may require a logged-in session "
            "or interactive browser pagination."
        )
    return "Recipe does not require a connected session."


def _expected_report_field_sources(recipe: JobBoardRecipe) -> dict[str, str]:
    sources = {"title": "listing.title_selector", "url": "listing.link_selector"}
    selector_fields = {
        "company": recipe.listing.company_selector,
        "location": recipe.listing.location_selector,
        "remote": recipe.listing.remote_selector,
        "rate": recipe.listing.rate_selector,
        "workload": recipe.listing.workload_selector,
        "posted_date": recipe.listing.posted_date_selector,
        "start_date": recipe.listing.start_date_selector,
        "description": recipe.listing.description_selector,
    }
    for field_name, selector in selector_fields.items():
        if _selectors(selector):
            sources[field_name] = f"listing.{field_name}_selector"
    pattern_fields = {
        "location": recipe.patterns.location_regex,
        "remote": recipe.patterns.remote_regex,
        "rate": recipe.patterns.rate_regex,
        "workload": recipe.patterns.workload_regex or recipe.patterns.work_type_regex,
        "posted_date": recipe.patterns.posted_date_regex,
        "start_date": recipe.patterns.start_date_regex,
        "languages": recipe.patterns.language_regex,
    }
    for field_name, pattern in pattern_fields.items():
        if pattern and field_name not in sources:
            sources[field_name] = f"patterns.{field_name}"
    for field_name in expected_detail_fields(recipe):
        sources.setdefault(field_name, "detail")
    return sources


def _job_field_present(job: Job, field_name: str) -> bool:
    value = getattr(job, field_name)
    if isinstance(value, list):
        return bool(value)
    if field_name == "description":
        return len(str(value).strip()) >= 40
    return bool(str(value).strip()) and str(value).strip() != "Not listed"


def _job_field_value(job: Job, field_name: str) -> str:
    value = getattr(job, field_name)
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)
