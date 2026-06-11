from __future__ import annotations

import json
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from job_agent.models import Job
from job_agent.services.recipes.discovery import (
    _pagination_url_key,
    _pagination_urls_to_fetch,
    find_pagination_links,
)
from job_agent.services.recipes.extraction import extract_jobs_with_recipe_with_stats
from job_agent.services.recipes.mapping import _first_selector
from job_agent.services.recipes.models import (
    JobBoardRecipe,
    ListingExtractionStats,
    PaginationLink,
    RecipeRunStep,
)

FetchHtmlForMode = Callable[..., tuple[str, str, list[str]]]
RecipeProgressCallback = Callable[[RecipeRunStep], None]


def fetch_pagination_job_pages(
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
    fetch_html_for_mode: FetchHtmlForMode,
    session_state_path: str | Path | None = None,
    material_log: Any | None = None,
    progress_callback: RecipeProgressCallback | None = None,
    step_collector: list[RecipeRunStep] | None = None,
) -> tuple[list[str], list[Job], list[str], list[ListingExtractionStats]]:
    additional_page_limit = None if max_pages is None else max(0, max_pages - 1)
    if not pagination_links or additional_page_limit == 0:
        return [], existing_jobs, [], []

    warnings: list[str] = []
    jobs = list(existing_jobs)
    seen_urls = {job.url for job in jobs if job.url}
    fetched_urls: list[str] = []
    page_stats: list[ListingExtractionStats] = []
    visited_page_keys = {_pagination_url_key(start_url)} if start_url else set()
    urls_to_fetch = [
        url
        for url in _pagination_urls_to_fetch(pagination_links, max_pages=max_pages)
        if _pagination_url_key(url) not in visited_page_keys
    ]
    queued_keys = {_pagination_url_key(url) for url in urls_to_fetch}
    page_total = 1 + len(urls_to_fetch)

    index = 0
    while urls_to_fetch:
        if additional_page_limit is not None and len(fetched_urls) >= additional_page_limit:
            break
        page_url = urls_to_fetch.pop(0)
        page_key = _pagination_url_key(page_url)
        queued_keys.discard(page_key)
        if page_key in visited_page_keys:
            continue
        visited_page_keys.add(page_key)
        if job_limit is not None and len(jobs) >= job_limit:
            break
        if index and recipe.pagination.request_delay_seconds:
            _emit_recipe_step(
                progress_callback,
                RecipeRunStep(
                    phase="Pagination delay",
                    status="running",
                    detail=f"Waiting {recipe.pagination.request_delay_seconds:g}s before fetching the next pagination page.",
                    capability="pagination",
                ),
            )
            time.sleep(recipe.pagination.request_delay_seconds)
        _emit_recipe_step(
            progress_callback,
            RecipeRunStep(
                phase="Pagination page request",
                status="running",
                detail=f"Fetching pagination page {page_url}.",
                capability="pagination",
            ),
        )
        try:
            html, final_url, fetch_warnings = fetch_html_for_mode(
                page_url,
                timeout_seconds,
                use_rendered=use_rendered,
                session_state_path=session_state_path,
            )
        except ValueError as exc:
            warnings.append(f"Pagination fetch failed for {page_url}: {exc}")
            _record_recipe_step(
                step_collector,
                progress_callback,
                RecipeRunStep(
                    phase="Pagination page failed",
                    status="failed",
                    detail=f"Could not fetch {page_url}: {exc}",
                    capability="pagination",
                ),
            )
            continue
        warnings.extend(fetch_warnings)
        final_key = _pagination_url_key(final_url)
        if final_key in visited_page_keys and final_key != page_key:
            _record_material_html(
                material_log,
                kind="pagination",
                url=page_url,
                final_url=final_url,
                html=html,
                mode="rendered_html" if use_rendered else "static_html",
                warnings=fetch_warnings,
                note="Skipped because the final URL resolved to a listing page already read.",
            )
            _record_recipe_step(
                step_collector,
                progress_callback,
                RecipeRunStep(
                    phase="Pagination page skipped",
                    status="skipped",
                    detail=f"Skipped {final_url}; it resolves to a listing page already read.",
                    capability="pagination",
                    page_explored_count=1 + len(fetched_urls),
                    page_total=page_total,
                    jobs_found=len(jobs),
                ),
            )
            continue
        visited_page_keys.add(final_key)
        fetched_urls.append(final_url)
        _record_material_html(
            material_log,
            kind="pagination",
            url=page_url,
            final_url=final_url,
            html=html,
            mode="rendered_html" if use_rendered else "static_html",
            warnings=fetch_warnings,
        )
        page_jobs, stats = extract_jobs_with_recipe_with_stats(
            html,
            base_url=final_url,
            recipe=recipe,
            use_recipe_card_limit=use_recipe_card_limit,
        )
        before_count = len(jobs)
        for job in page_jobs:
            if job.url in seen_urls:
                stats.duplicate_count += 1
                continue
            seen_urls.add(job.url)
            jobs.append(job)
            if job_limit is not None and len(jobs) >= job_limit:
                stats.limit_skipped_count += max(0, len(page_jobs) - (len(jobs) - before_count))
                break
        stats.extracted_jobs = len(jobs) - before_count
        if page_jobs and stats.duplicate_count and stats.extracted_jobs == 0:
            warnings.append(
                f"Pagination page {final_url} returned {stats.duplicate_count} duplicate listing(s) "
                "already seen on earlier pages and added no new jobs."
            )
        page_stats.append(stats)
        for next_url in _pagination_urls_to_fetch(find_pagination_links(html, final_url, recipe), max_pages=None):
            next_key = _pagination_url_key(next_url)
            if next_key in visited_page_keys or next_key in queued_keys:
                continue
            if additional_page_limit is not None and len(fetched_urls) + len(urls_to_fetch) >= additional_page_limit:
                break
            urls_to_fetch.append(next_url)
            queued_keys.add(next_key)
        page_total = max(page_total, 1 + len(fetched_urls) + len(urls_to_fetch))
        _record_recipe_step(
            step_collector,
            progress_callback,
            RecipeRunStep(
                phase="Pagination page fetched",
                status="completed",
                detail=f"Fetched {final_url}; added {len(jobs) - before_count} new job(s).",
                capability="pagination",
                page_explored_count=1 + len(fetched_urls),
                page_total=page_total,
                jobs_found=len(jobs),
            ),
        )
        index += 1
    return warnings, jobs, fetched_urls, page_stats


def fetch_ajax_pagination_job_pages(
    base_url: str,
    recipe: JobBoardRecipe,
    *,
    use_rendered: bool,
    timeout_seconds: int,
    max_pages: int | None,
    existing_jobs: list[Job],
    job_limit: int | None,
    use_recipe_card_limit: bool,
    fetch_html_for_mode: FetchHtmlForMode,
    session_state_path: str | Path | None = None,
    material_log: Any | None = None,
    progress_callback: RecipeProgressCallback | None = None,
    step_collector: list[RecipeRunStep] | None = None,
) -> tuple[list[str], list[Job], list[str], list[ListingExtractionStats]]:
    urls_to_fetch = ajax_urls_to_fetch(base_url, recipe, max_pages=max_pages)
    if not urls_to_fetch:
        return ["AJAX pagination is configured, but no AJAX page URLs could be generated."], existing_jobs, [], []

    warnings: list[str] = []
    jobs = list(existing_jobs)
    seen_urls = {job.url for job in jobs if job.url}
    fetched_urls: list[str] = []
    page_stats: list[ListingExtractionStats] = []
    page_total = 1 + len(urls_to_fetch)
    for index, page_url in enumerate(urls_to_fetch):
        if job_limit is not None and len(jobs) >= job_limit:
            break
        if index and recipe.pagination.request_delay_seconds:
            _emit_recipe_step(
                progress_callback,
                RecipeRunStep(
                    phase="AJAX pagination delay",
                    status="running",
                    detail=f"Waiting {recipe.pagination.request_delay_seconds:g}s before fetching the next AJAX page.",
                    capability="pagination",
                ),
            )
            time.sleep(recipe.pagination.request_delay_seconds)
        _emit_recipe_step(
            progress_callback,
            RecipeRunStep(
                phase="AJAX pagination request",
                status="running",
                detail=f"Fetching AJAX pagination page {page_url}.",
                capability="pagination",
            ),
        )
        try:
            html, final_url, fetch_warnings = fetch_html_for_mode(
                page_url,
                timeout_seconds,
                use_rendered=use_rendered,
                session_state_path=session_state_path,
            )
        except ValueError as exc:
            warnings.append(f"AJAX pagination fetch failed for {page_url}: {exc}")
            _record_recipe_step(
                step_collector,
                progress_callback,
                RecipeRunStep(
                    phase="AJAX pagination failed",
                    status="failed",
                    detail=f"Could not fetch {page_url}: {exc}",
                    capability="pagination",
                ),
            )
            continue
        warnings.extend(fetch_warnings)
        fetched_urls.append(final_url)
        _record_material_html(
            material_log,
            kind="ajax_pagination",
            url=page_url,
            final_url=final_url,
            html=html,
            mode="rendered_html" if use_rendered else "static_html",
            warnings=fetch_warnings,
        )
        payload_html = ajax_response_html(html)
        page_jobs, stats = extract_jobs_with_recipe_with_stats(
            payload_html,
            base_url=final_url,
            recipe=recipe,
            use_recipe_card_limit=use_recipe_card_limit,
        )
        before_count = len(jobs)
        merge_pagination_jobs(page_jobs, stats, jobs, seen_urls, job_limit)
        if page_jobs and stats.duplicate_count and stats.extracted_jobs == 0:
            warnings.append(
                f"AJAX pagination page {final_url} returned {stats.duplicate_count} duplicate listing(s) "
                "already seen on earlier pages and added no new jobs."
            )
        page_stats.append(stats)
        _record_recipe_step(
            step_collector,
            progress_callback,
            RecipeRunStep(
                phase="AJAX pagination fetched",
                status="completed",
                detail=f"Fetched {final_url}; added {len(jobs) - before_count} new job(s).",
                capability="pagination",
                page_explored_count=1 + len(fetched_urls),
                page_total=page_total,
                jobs_found=len(jobs),
            ),
        )
    return warnings, jobs, fetched_urls, page_stats


def fetch_browser_click_pagination_job_pages(
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
    progress_callback: RecipeProgressCallback | None = None,
    step_collector: list[RecipeRunStep] | None = None,
) -> tuple[list[str], list[Job], list[str], list[ListingExtractionStats]]:
    click_selector = (
        _first_selector(recipe.pagination.click_selector)
        or _first_selector(recipe.pagination.next_selector)
        or _first_selector(recipe.pagination.page_link_selector)
    )
    if not click_selector:
        return ["Browser-click pagination is configured, but no click selector is available."], existing_jobs, [], []

    additional_page_limit = max(0, (max_pages if max_pages is not None else recipe.pagination.max_pages) - 1)
    if additional_page_limit == 0:
        return [], existing_jobs, [], []

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return [f"Browser-click pagination requires Playwright: {exc}"], existing_jobs, [], []

    warnings: list[str] = []
    jobs = list(existing_jobs)
    seen_urls = {job.url for job in jobs if job.url}
    fetched_urls: list[str] = []
    page_stats: list[ListingExtractionStats] = []
    timeout_ms = max(1, timeout_seconds) * 1000
    page_total = 1 + additional_page_limit
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page_options: dict[str, Any] = {}
                if session_state_path and Path(session_state_path).exists():
                    page_options["storage_state"] = str(session_state_path)
                page = browser.new_page(**page_options)
                page.goto(start_url, wait_until="domcontentloaded", timeout=timeout_ms)
                _record_material_html(
                    material_log,
                    kind="browser_pagination_start",
                    url=start_url,
                    final_url=page.url,
                    html=page.content(),
                    mode="rendered_html",
                )
                for index in range(additional_page_limit):
                    if job_limit is not None and len(jobs) >= job_limit:
                        break
                    if index and recipe.pagination.request_delay_seconds:
                        _emit_recipe_step(
                            progress_callback,
                            RecipeRunStep(
                                phase="Browser pagination delay",
                                status="running",
                                detail=f"Waiting {recipe.pagination.request_delay_seconds:g}s before the next click.",
                                capability="pagination",
                            ),
                        )
                        time.sleep(recipe.pagination.request_delay_seconds)
                    _emit_recipe_step(
                        progress_callback,
                        RecipeRunStep(
                            phase="Browser pagination click",
                            status="running",
                            detail=f"Clicking pagination control {click_selector}.",
                            capability="pagination",
                        ),
                    )
                    try:
                        with suppress(PlaywrightError):
                            page.wait_for_selector(click_selector, state="attached", timeout=timeout_ms)
                        locator = page.locator(click_selector).first
                        all_matches = page.locator(click_selector)
                        if all_matches.count() <= 0:
                            warnings.append(f"Browser-click pagination control was not found: {click_selector}")
                            break
                        for match_index in range(all_matches.count()):
                            candidate = all_matches.nth(match_index)
                            if candidate.is_visible():
                                locator = candidate
                                break
                        before_html = page.content()
                        locator.click(timeout=timeout_ms)
                        try:
                            page.wait_for_load_state("networkidle", timeout=timeout_ms)
                        except PlaywrightError:
                            page.wait_for_timeout(750)
                        html = page.content()
                    except PlaywrightError as exc:
                        warnings.append(f"Browser-click pagination failed for {click_selector}: {exc}")
                        break
                    final_url = page.url
                    fetched_urls.append(final_url)
                    _record_material_html(
                        material_log,
                        kind="browser_pagination",
                        url=start_url,
                        final_url=final_url,
                        html=html,
                        mode="rendered_html",
                        note=f"After click {index + 1} using {click_selector}.",
                    )
                    page_jobs, stats = extract_jobs_with_recipe_with_stats(
                        html,
                        base_url=final_url,
                        recipe=recipe,
                        use_recipe_card_limit=use_recipe_card_limit,
                    )
                    before_count = len(jobs)
                    merge_pagination_jobs(page_jobs, stats, jobs, seen_urls, job_limit)
                    if before_html == html and stats.extracted_jobs == 0:
                        warnings.append(
                            "Browser-click pagination did not change the listing page. "
                            "The click selector or session state may be wrong."
                        )
                    if page_jobs and stats.duplicate_count and stats.extracted_jobs == 0:
                        warnings.append(
                            f"Browser-click pagination page {final_url} returned {stats.duplicate_count} duplicate "
                            "listing(s) already seen on earlier pages and added no new jobs."
                        )
                    page_stats.append(stats)
                    _record_recipe_step(
                        step_collector,
                        progress_callback,
                        RecipeRunStep(
                            phase="Browser pagination page read",
                            status="completed",
                            detail=f"Read browser pagination page {final_url}; added {len(jobs) - before_count} new job(s).",
                            capability="pagination",
                            page_explored_count=1 + len(fetched_urls),
                            page_total=page_total,
                            jobs_found=len(jobs),
                        ),
                    )
            finally:
                browser.close()
    except PlaywrightError as exc:
        warnings.append(f"Browser-click pagination could not start: {exc}")
    return warnings, jobs, fetched_urls, page_stats


def pagination_trace_detail(
    recipe: JobBoardRecipe,
    configured_links: list[PaginationLink],
    observed_links: list[PaginationLink],
    fetched_urls: list[str],
    interactive_control_count: int = 0,
) -> str:
    if not pagination_expected(recipe):
        if observed_links or interactive_control_count:
            return (
                f"Observed {len(observed_links)} pagination-looking link(s) and {interactive_control_count} "
                "interactive pagination control(s), but pagination is not expected by this recipe."
            )
        return "Recipe has no pagination selectors configured."
    detail = (
        f"Pagination strategy {recipe.pagination.strategy}; configured pagination selectors found "
        f"{len(configured_links)} link(s); independent observation found {len(observed_links)} "
        f"pagination-looking link(s) and {interactive_control_count} interactive control(s)."
    )
    if fetched_urls:
        detail += f" Proof fetched {len(fetched_urls)} pagination page(s)."
    elif recipe.pagination.strategy in {"ajax", "browser_click"}:
        detail += " No pagination proof pages were fetched for this configured strategy."
    return detail


def pagination_step_status(
    recipe: JobBoardRecipe,
    configured_links: list[PaginationLink],
    fetched_urls: list[str],
    interactive_control_count: int = 0,
) -> str:
    if fetched_urls:
        return "completed"
    if configured_links:
        return "completed"
    if recipe.pagination.strategy in {"ajax", "browser_click"}:
        return "warning"
    if interactive_control_count:
        return "warning"
    return "skipped"


def pagination_expected(recipe: JobBoardRecipe) -> bool:
    return bool(
        recipe.pagination.strategy in {"ajax", "browser_click"}
        or recipe.pagination.page_link_selector
        or recipe.pagination.next_selector
    )


def merge_pagination_jobs(
    page_jobs: list[Job],
    stats: ListingExtractionStats,
    jobs: list[Job],
    seen_urls: set[str],
    job_limit: int | None,
) -> None:
    before_count = len(jobs)
    for job in page_jobs:
        if job.url in seen_urls:
            stats.duplicate_count += 1
            continue
        seen_urls.add(job.url)
        jobs.append(job)
        if job_limit is not None and len(jobs) >= job_limit:
            stats.limit_skipped_count += max(0, len(page_jobs) - (len(jobs) - before_count))
            break
    stats.extracted_jobs = len(jobs) - before_count


def ajax_urls_to_fetch(base_url: str, recipe: JobBoardRecipe, *, max_pages: int | None) -> list[str]:
    template = recipe.pagination.ajax_url_template.strip()
    if not template:
        return []
    effective_max_pages = max_pages if max_pages is not None else recipe.pagination.max_pages
    if effective_max_pages <= 1:
        return []
    urls: list[str] = []
    for page_number in range(2, effective_max_pages + 1):
        values = {
            "page": page_number,
            "page_index": page_number - 1,
            "offset": page_number - 1,
        }
        try:
            raw_url = template.format(**values)
        except (KeyError, IndexError, ValueError):
            raw_url = template
        urls.append(urljoin(base_url, raw_url))
    return urls


def ajax_response_html(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    fragments: list[str] = []
    _collect_html_strings(payload, fragments)
    return "\n".join(fragments) if fragments else text


def _collect_html_strings(value: Any, fragments: list[str]) -> None:
    if isinstance(value, str):
        if "<" in value and ">" in value:
            fragments.append(value)
        return
    if isinstance(value, dict):
        for child in value.values():
            _collect_html_strings(child, fragments)
        return
    if isinstance(value, list):
        for child in value:
            _collect_html_strings(child, fragments)


def _record_recipe_step(
    steps: list[RecipeRunStep] | None,
    progress_callback: RecipeProgressCallback | None,
    step: RecipeRunStep,
) -> None:
    if steps is not None:
        steps.append(step)
    _emit_recipe_step(progress_callback, step)


def _emit_recipe_step(progress_callback: RecipeProgressCallback | None, step: RecipeRunStep) -> None:
    if progress_callback:
        progress_callback(step)


def _record_material_html(material_log: Any, **kwargs: Any) -> None:
    if not material_log:
        return
    recorder = getattr(material_log, "record_html", None)
    if callable(recorder):
        recorder(**kwargs)
