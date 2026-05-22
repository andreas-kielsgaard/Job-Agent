from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import MISSING, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup, Tag

from job_agent.models import Job
from job_agent.services.extraction_quality import ExtractionQuality, candidate_quality, title_quality
from job_agent.services.job_board_check_service import validate_public_url

SelectorValue = str | list[str]
VALID_MODES = {"static_html", "rendered_html"}


@dataclass
class ListingRecipe:
    card_selector: str
    title_selector: SelectorValue
    link_selector: SelectorValue
    company_selector: SelectorValue = ""
    location_selector: SelectorValue = ""
    remote_selector: SelectorValue = ""
    rate_selector: SelectorValue = ""
    workload_selector: SelectorValue = ""
    posted_date_selector: SelectorValue = ""
    description_selector: SelectorValue = ""


@dataclass
class DetailRecipe:
    follow: bool = False
    description_selector: SelectorValue = ""
    title_selector: SelectorValue = ""
    location_selector: SelectorValue = ""
    remote_selector: SelectorValue = ""
    rate_selector: SelectorValue = ""
    workload_selector: SelectorValue = ""
    posted_date_selector: SelectorValue = ""
    start_date_selector: SelectorValue = ""
    language_selector: SelectorValue = ""
    max_detail_pages: int = 5
    request_delay_seconds: float = 0.0
    use_json_ld: bool = False


@dataclass
class PaginationRecipe:
    page_link_selector: SelectorValue = ""
    next_selector: SelectorValue = ""
    max_pages: int = 1
    request_delay_seconds: float = 1.0


@dataclass
class ApplicationEntry:
    label: str
    url: str
    kind: str
    detail: str = ""


@dataclass
class DetailPageAttempt:
    url: str
    status: str
    found_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class RecipeRunStep:
    phase: str
    status: str
    detail: str
    capability: str = ""


@dataclass
class ListingExtractionStats:
    page_url: str
    observed_cards: int = 0
    extracted_jobs: int = 0
    missing_url_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    limit_skipped_count: int = 0
    limit: int = 0


@dataclass
class RecipeFieldCheck:
    field: str
    scope: str
    expected: bool
    status: str
    detail: str
    present_count: int = 0
    total_count: int = 0
    sample_value: str = ""
    source: str = ""

    @property
    def label(self) -> str:
        return self.field.replace("_", " ").title()


@dataclass
class RecipeCapabilityCheck:
    capability: str
    status: str
    expected: bool
    observed: bool
    detail: str

    @property
    def label(self) -> str:
        labels = {
            "listing_cards": "Listing cards",
            "job_urls": "Job URLs",
            "pagination_detection": "Pagination detection",
            "pagination_navigation": "Pagination navigation",
            "detail_navigation": "Detail navigation",
            "application_entry": "Application entry",
        }
        return labels.get(self.capability, self.capability.replace("_", " ").capitalize())


@dataclass
class AcceptRecipe:
    title_contains: list[str] = field(default_factory=list)
    url_contains: list[str] = field(default_factory=list)


@dataclass
class RejectRecipe:
    title_exact: list[str] = field(default_factory=list)
    title_contains: list[str] = field(default_factory=list)
    url_contains: list[str] = field(default_factory=list)


@dataclass
class LimitRecipe:
    max_cards: int = 25
    min_title_length: int = 8
    min_description_length: int = 0


@dataclass
class PatternsRecipe:
    title_regex: str = ""
    job_id_regex: str = ""
    location_regex: str = ""
    remote_regex: str = ""
    rate_regex: str = ""
    workload_regex: str = ""
    posted_date_regex: str = ""
    start_date_regex: str = ""
    language_regex: str = ""
    work_type_regex: str = ""


@dataclass
class JobBoardRecipe:
    source_name: str
    listing: ListingRecipe
    start_url: str = ""
    mode: str = "static_html"
    accept: AcceptRecipe = field(default_factory=AcceptRecipe)
    detail: DetailRecipe = field(default_factory=DetailRecipe)
    pagination: PaginationRecipe = field(default_factory=PaginationRecipe)
    reject: RejectRecipe = field(default_factory=RejectRecipe)
    limits: LimitRecipe = field(default_factory=LimitRecipe)
    patterns: PatternsRecipe = field(default_factory=PatternsRecipe)


@dataclass
class PaginationLink:
    label: str
    url: str
    is_next: bool = False


@dataclass
class RecipeExtractionResult:
    jobs: list[Job]
    base_url: str
    mode_used: str
    warnings: list[str] = field(default_factory=list)
    pagination_links: list[PaginationLink] = field(default_factory=list)
    observed_pagination_links: list[PaginationLink] = field(default_factory=list)
    application_entries: list[ApplicationEntry] = field(default_factory=list)
    detail_attempts: list[DetailPageAttempt] = field(default_factory=list)
    steps: list[RecipeRunStep] = field(default_factory=list)
    field_checks: list[RecipeFieldCheck] = field(default_factory=list)
    capability_checks: list[RecipeCapabilityCheck] = field(default_factory=list)
    detail_fetch_limit: int | None = None
    detail_fetch_count: int = 0
    detail_enriched_count: int = 0
    pagination_fetch_count: int = 0
    pagination_fetch_attempts: list[str] = field(default_factory=list)
    listing_pages: list[ListingExtractionStats] = field(default_factory=list)
    listing_observed_count: int = 0
    listing_extracted_count: int = 0
    listing_missing_url_count: int = 0
    listing_rejected_count: int = 0
    listing_duplicate_count: int = 0
    listing_limit_skipped_count: int = 0


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


def load_job_board_recipe(path: Path) -> JobBoardRecipe:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Recipe {path} must be a YAML mapping.")
    return job_board_recipe_from_mapping(data, label=str(path))


def job_board_recipe_from_mapping(data: dict[str, Any], label: str = "recipe") -> JobBoardRecipe:
    listing_data = data.get("listing") or {}
    if not isinstance(listing_data, dict):
        raise ValueError(f"{label}: listing must be a mapping.")
    missing = [
        key
        for key in ["card_selector", "title_selector", "link_selector"]
        if not _has_selector_value(listing_data.get(key, ""))
    ]
    if missing:
        raise ValueError(f"{label}: missing required listing selector(s): {', '.join(missing)}.")

    mode = str(data.get("mode") or "static_html").strip()
    if mode not in VALID_MODES:
        raise ValueError(f"{label}: mode must be one of: {', '.join(sorted(VALID_MODES))}.")

    recipe = JobBoardRecipe(
        source_name=str(data.get("source_name") or "Recipe source").strip(),
        start_url=str(data.get("start_url") or "").strip(),
        mode=mode,
        listing=ListingRecipe(**_selector_fields(listing_data, ListingRecipe)),
        accept=AcceptRecipe(**_list_fields(_mapping_section(data, "accept"), AcceptRecipe, "accept")),
        detail=DetailRecipe(**_selector_fields(_mapping_section(data, "detail"), DetailRecipe)),
        pagination=PaginationRecipe(**_selector_fields(_mapping_section(data, "pagination"), PaginationRecipe)),
        reject=RejectRecipe(**_list_fields(_mapping_section(data, "reject"), RejectRecipe, "reject")),
        limits=LimitRecipe(**_int_fields(_mapping_section(data, "limits"), LimitRecipe)),
        patterns=PatternsRecipe(**_regex_fields(_mapping_section(data, "patterns"), PatternsRecipe, label)),
    )
    _validate_positive_int(recipe.limits.max_cards, "limits.max_cards", label)
    _validate_positive_int(recipe.detail.max_detail_pages, "detail.max_detail_pages", label)
    _validate_positive_int(recipe.pagination.max_pages, "pagination.max_pages", label)
    _validate_positive_int(recipe.limits.min_title_length, "limits.min_title_length", label)
    if recipe.limits.min_description_length < 0:
        raise ValueError(f"{label}: limits.min_description_length must be zero or greater.")
    if recipe.detail.request_delay_seconds < 0:
        raise ValueError(f"{label}: detail.request_delay_seconds must be zero or greater.")
    if recipe.pagination.request_delay_seconds < 0:
        raise ValueError(f"{label}: pagination.request_delay_seconds must be zero or greater.")
    return recipe


def extract_jobs_with_recipe(
    html: str,
    base_url: str,
    recipe: JobBoardRecipe,
    source_name: str = "",
) -> list[Job]:
    jobs, _stats = _extract_jobs_with_recipe_with_stats(html, base_url, recipe, source_name=source_name)
    return jobs


def _extract_jobs_with_recipe_with_stats(
    html: str,
    base_url: str,
    recipe: JobBoardRecipe,
    source_name: str = "",
) -> tuple[list[Job], ListingExtractionStats]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    cards = soup.select(recipe.listing.card_selector)
    stats = ListingExtractionStats(page_url=base_url, observed_cards=len(cards), limit=recipe.limits.max_cards)

    for index, card in enumerate(cards):
        title = _select_text(card, recipe.listing.title_selector)
        link = _select_href(card, recipe.listing.link_selector)
        url = urljoin(base_url, link) if link else ""
        description = (
            _select_text(card, recipe.listing.description_selector) if recipe.listing.description_selector else ""
        )
        raw_text = card.get_text("\n", strip=True)
        if not description:
            description = raw_text

        pattern_values = _extract_pattern_values(raw_text, recipe.patterns)
        title = pattern_values.get("title") or title

        if not url:
            stats.missing_url_count += 1
            continue
        if _should_reject(title, url, description, recipe):
            stats.rejected_count += 1
            continue
        if url in seen_urls:
            stats.duplicate_count += 1
            continue
        seen_urls.add(url)

        posted_date = _select_text(card, recipe.listing.posted_date_selector)
        job = Job(
            title=title,
            company=_select_text(card, recipe.listing.company_selector) or "Unknown",
            source=source_name or recipe.source_name,
            url=url,
            application_url=url,
            location=_select_text(card, recipe.listing.location_selector) or pattern_values.get("location") or "Not listed",
            remote=_select_text(card, recipe.listing.remote_selector) or pattern_values.get("remote") or "Not listed",
            rate=_select_text(card, recipe.listing.rate_selector) or pattern_values.get("rate") or "Not listed",
            start_date=pattern_values.get("start_date") or "Not listed",
            workload=(
                _select_text(card, recipe.listing.workload_selector)
                or pattern_values.get("workload")
                or pattern_values.get("work_type")
                or "Not listed"
            ),
            posted_date=posted_date or pattern_values.get("posted_date") or "Not listed",
            languages=[pattern_values["language"]] if pattern_values.get("language") else [],
            description=description[:3000],
            raw_text=raw_text[:5000],
            source_confidence="recipe",
            freshness_confidence="recipe" if posted_date or pattern_values.get("posted_date") else "unknown",
            extraction_notes=_extraction_notes(pattern_values),
        )
        jobs.append(job)
        if len(jobs) >= recipe.limits.max_cards:
            stats.limit_skipped_count += max(0, len(cards) - index - 1)
            break
    stats.extracted_jobs = len(jobs)
    return jobs, stats


def extract_jobs_with_recipe_from_html(
    html: str,
    base_url: str,
    recipe: JobBoardRecipe,
    mode_used: str = "local_fixture_html",
    warnings: list[str] | None = None,
) -> RecipeExtractionResult:
    jobs, listing_stats = _extract_jobs_with_recipe_with_stats(html, base_url=base_url, recipe=recipe)
    pagination_links = find_pagination_links(html, base_url, recipe)
    result = RecipeExtractionResult(
        jobs=jobs,
        base_url=base_url,
        mode_used=mode_used,
        warnings=list(warnings or []),
        pagination_links=pagination_links,
        observed_pagination_links=discover_pagination_links(html, base_url),
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
                status="completed" if pagination_links else "skipped",
                detail=_pagination_trace_detail(recipe, pagination_links, discover_pagination_links(html, base_url), []),
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
    fetch_pagination: bool = False,
    pagination_page_limit: int | None = None,
    job_limit: int | None = None,
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
    html, final_url, warnings = (
        _fetch_rendered_html(normalized_url, timeout_seconds)
        if use_rendered
        else _fetch_static_html(normalized_url, timeout_seconds)
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
    jobs, listing_stats = _extract_jobs_with_recipe_with_stats(html, base_url=final_url, recipe=recipe)
    jobs = _limited_jobs(jobs, job_limit)
    if listing_stats.extracted_jobs > len(jobs):
        listing_stats.limit_skipped_count += listing_stats.extracted_jobs - len(jobs)
        listing_stats.extracted_jobs = len(jobs)
    pagination_links = find_pagination_links(html, final_url, recipe)
    observed_pagination_links = discover_pagination_links(html, final_url)
    application_entries = discover_application_entries(html, final_url)
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Listing selectors applied",
            status="completed" if jobs else "warning",
            detail=f"Extracted {len(jobs)} unique job(s) from the first listing page.",
            capability="listing",
        ),
    )

    pagination_fetch_attempts: list[str] = []
    pagination_listing_stats: list[ListingExtractionStats] = []
    if fetch_pagination:
        page_limit = pagination_page_limit if pagination_page_limit is not None else recipe.pagination.max_pages
        page_warnings, fetched_jobs, fetched_urls, fetched_stats = _fetch_pagination_job_pages(
            pagination_links,
            recipe,
            use_rendered=use_rendered,
            timeout_seconds=timeout_seconds,
            max_pages=page_limit,
            existing_jobs=jobs,
            job_limit=job_limit,
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
            status="completed" if pagination_links else "skipped",
            detail=_pagination_trace_detail(recipe, pagination_links, observed_pagination_links, pagination_fetch_attempts),
            capability="pagination",
        ),
    )

    detail_limit = recipe.detail.max_detail_pages if use_recipe_detail_limit else detail_page_limit
    detail_warnings, detail_attempts = _enrich_jobs_with_detail_pages_with_trace(
        jobs,
        recipe,
        timeout_seconds=timeout_seconds,
        detail_page_limit=detail_limit,
        progress_callback=progress_callback,
        step_collector=steps,
    )
    warnings.extend(detail_warnings)
    detail_enriched_count = sum(1 for attempt in detail_attempts if attempt.found_fields)
    _record_recipe_step(
        steps,
        progress_callback,
        RecipeRunStep(
            phase="Detail page enrichment",
            status="completed" if detail_enriched_count else "skipped" if not recipe.detail.follow else "warning",
            detail=_detail_trace_detail(recipe, detail_limit, detail_attempts),
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
        pagination_fetch_count=len(pagination_fetch_attempts),
        pagination_fetch_attempts=pagination_fetch_attempts,
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


def _enrich_jobs_with_detail_pages_with_trace(
    jobs: list[Job],
    recipe: JobBoardRecipe,
    timeout_seconds: int = 15,
    detail_page_limit: int | None = None,
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
    target_jobs = jobs if detail_page_limit is None else jobs[:detail_page_limit]
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
            response = requests.get(
                job.url,
                timeout=timeout_seconds,
                headers={"User-Agent": "Job-Agent recipe detail fetcher (public page; low volume)"},
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
    return warnings, attempts


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


def find_pagination_links(html: str, base_url: str, recipe: JobBoardRecipe) -> list[PaginationLink]:
    selectors = _selectors(recipe.pagination.page_link_selector) + _selectors(recipe.pagination.next_selector)
    if not selectors:
        return []

    soups = _selectable_soups(html)
    next_urls: set[str] = set()
    for soup in soups:
        next_urls.update(_selected_urls(soup, recipe.pagination.next_selector, base_url))
    links: list[PaginationLink] = []
    seen_urls: set[str] = set()
    for soup in soups:
        for selector in selectors:
            for match in soup.select(selector):
                href = match.get("href")
                if not href:
                    continue
                url = urljoin(base_url, str(href).strip())
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                label = match.get_text(" ", strip=True) or url
                links.append(
                    PaginationLink(
                        label=label,
                        url=url,
                        is_next=url in next_urls or _looks_like_next_link(label, match),
                    )
                )
    return links


def discover_pagination_links(html: str, base_url: str) -> list[PaginationLink]:
    links: list[PaginationLink] = []
    seen_urls: set[str] = set()
    for soup in _selectable_soups(html):
        for match in soup.find_all("a", href=True):
            label = match.get_text(" ", strip=True)
            href = str(match.get("href", "")).strip()
            haystack = " ".join(
                [
                    label,
                    href,
                    " ".join(match.get("class", [])),
                    str(match.get("rel") or ""),
                    str(match.get("aria-label") or ""),
                ]
            ).lower()
            if not _looks_like_pagination(label, href, haystack):
                continue
            url = urljoin(base_url, href)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            links.append(PaginationLink(label=label or url, url=url, is_next=_looks_like_next_link(label, match)))
    return links


def discover_application_entries(html: str, base_url: str) -> list[ApplicationEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[ApplicationEntry] = []
    seen: set[tuple[str, str]] = set()
    for element in soup.find_all(["a", "button", "input"]):
        label = element.get_text(" ", strip=True) or str(element.get("value") or element.get("aria-label") or "")
        href = str(element.get("href") or "").strip()
        onclick = str(element.get("onclick") or element.get("onClick") or "")
        data_attrs = " ".join(str(value) for key, value in element.attrs.items() if str(key).startswith("data-"))
        haystack = " ".join([label, href, onclick, data_attrs, " ".join(element.get("class", []))]).lower()
        if not any(term in haystack for term in ["apply", "application", "contact-button", "send application"]):
            continue
        if element.name == "input" and str(element.get("type") or "").lower() in {"hidden", "checkbox"}:
            continue
        url = urljoin(base_url, href) if href and not href.startswith("javascript:") else ""
        key = (label, url or onclick)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            ApplicationEntry(
                label=label.strip() or "Application entry",
                url=url,
                kind=element.name or "element",
                detail=onclick[:180] if onclick else data_attrs[:180],
            )
        )
    return entries


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
    progress_callback: Callable[[RecipeRunStep], None] | None = None,
    step_collector: list[RecipeRunStep] | None = None,
) -> tuple[list[str], list[Job], list[str], list[ListingExtractionStats]]:
    if not pagination_links or not max_pages or max_pages <= 1:
        return [], existing_jobs, [], []

    warnings: list[str] = []
    jobs = list(existing_jobs)
    seen_urls = {job.url for job in jobs if job.url}
    fetched_urls: list[str] = []
    page_stats: list[ListingExtractionStats] = []
    urls_to_fetch = _pagination_urls_to_fetch(pagination_links, max_pages=max_pages)

    for index, page_url in enumerate(urls_to_fetch):
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
            html, final_url, fetch_warnings = (
                _fetch_rendered_html(page_url, timeout_seconds)
                if use_rendered
                else _fetch_static_html(page_url, timeout_seconds)
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
        fetched_urls.append(final_url)
        page_jobs, stats = _extract_jobs_with_recipe_with_stats(html, base_url=final_url, recipe=recipe)
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
        page_stats.append(stats)
        _record_recipe_step(
            step_collector,
            progress_callback,
            RecipeRunStep(
                phase="Pagination page fetched",
                status="completed",
                detail=f"Fetched {final_url}; added {len(jobs) - before_count} new job(s).",
                capability="pagination",
            ),
        )
    return warnings, jobs, fetched_urls, page_stats


def _pagination_urls_to_fetch(links: list[PaginationLink], max_pages: int) -> list[str]:
    additional_page_count = max(0, max_pages - 1)
    ordered = sorted(links, key=lambda link: (not link.is_next, link.url))
    urls: list[str] = []
    seen: set[str] = set()
    for link in ordered:
        if link.url in seen:
            continue
        seen.add(link.url)
        urls.append(link.url)
        if len(urls) >= additional_page_count:
            break
    return urls


def _pagination_trace_detail(
    recipe: JobBoardRecipe,
    configured_links: list[PaginationLink],
    observed_links: list[PaginationLink],
    fetched_urls: list[str],
) -> str:
    if not (recipe.pagination.page_link_selector or recipe.pagination.next_selector):
        if observed_links:
            return f"Observed {len(observed_links)} pagination-looking link(s), but pagination is not expected by this recipe."
        return "Recipe has no pagination selectors configured."
    detail = (
        f"Configured pagination selectors found {len(configured_links)} link(s); "
        f"independent observation found {len(observed_links)} pagination-looking link(s)."
    )
    if fetched_urls:
        detail += f" Proof fetched {len(fetched_urls)} pagination page(s)."
    return detail


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
    return f"Attempted {len(attempts)} detail page(s), allowed {limit_text}; {enriched} yielded configured detail fields."


def _attach_recipe_checks(result: RecipeExtractionResult, recipe: JobBoardRecipe) -> None:
    result.field_checks = _field_checks(result.jobs, recipe)
    result.capability_checks = _capability_checks(result, recipe)


def _attach_listing_counts(result: RecipeExtractionResult) -> None:
    result.listing_observed_count = sum(page.observed_cards for page in result.listing_pages)
    result.listing_extracted_count = sum(page.extracted_jobs for page in result.listing_pages)
    result.listing_missing_url_count = sum(page.missing_url_count for page in result.listing_pages)
    result.listing_rejected_count = sum(page.rejected_count for page in result.listing_pages)
    result.listing_duplicate_count = sum(page.duplicate_count for page in result.listing_pages)
    result.listing_limit_skipped_count = sum(page.limit_skipped_count for page in result.listing_pages)


def _capability_checks(result: RecipeExtractionResult, recipe: JobBoardRecipe) -> list[RecipeCapabilityCheck]:
    pagination_expected = bool(recipe.pagination.page_link_selector or recipe.pagination.next_selector)
    detail_expected = recipe.detail.follow
    application_observed = bool(result.application_entries)
    checks = [
        RecipeCapabilityCheck(
            capability="listing_cards",
            status="pass" if result.jobs else "fail",
            expected=True,
            observed=bool(result.jobs),
            detail=f"{len(result.jobs)} job(s) extracted from configured listing cards.",
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
                if pagination_expected and result.pagination_links
                else "fail"
                if pagination_expected
                else "observed"
                if result.observed_pagination_links
                else "not_expected"
            ),
            expected=pagination_expected,
            observed=bool(result.pagination_links or result.observed_pagination_links),
            detail=(
                f"Recipe found {len(result.pagination_links)} configured pagination link(s); "
                f"observed {len(result.observed_pagination_links)} pagination-looking link(s)."
            ),
        ),
        RecipeCapabilityCheck(
            capability="pagination_navigation",
            status=(
                "pass"
                if pagination_expected and result.pagination_fetch_count
                else "skipped"
                if pagination_expected and result.pagination_links
                else "fail"
                if pagination_expected
                else "not_expected"
            ),
            expected=pagination_expected,
            observed=bool(result.pagination_fetch_count),
            detail=(
                f"Fetched {result.pagination_fetch_count} pagination page(s) as proof."
                if result.pagination_fetch_count
                else "Pagination was not proof-fetched in this run."
            ),
        ),
        RecipeCapabilityCheck(
            capability="detail_navigation",
            status=(
                "pass"
                if detail_expected and result.detail_attempts
                else "fail"
                if detail_expected
                else "not_expected"
            ),
            expected=detail_expected,
            observed=bool(result.detail_attempts),
            detail=(
                f"Attempted {len(result.detail_attempts)} detail page(s); "
                f"{result.detail_enriched_count} yielded configured detail fields."
                if detail_expected
                else "Recipe does not request job detail pages."
            ),
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
    return checks


def _field_checks(jobs: list[Job], recipe: JobBoardRecipe) -> list[RecipeFieldCheck]:
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


def _expected_report_field_sources(recipe: JobBoardRecipe) -> dict[str, str]:
    sources = {"title": "listing.title_selector", "url": "listing.link_selector"}
    selector_fields = {
        "company": recipe.listing.company_selector,
        "location": recipe.listing.location_selector,
        "remote": recipe.listing.remote_selector,
        "rate": recipe.listing.rate_selector,
        "workload": recipe.listing.workload_selector,
        "posted_date": recipe.listing.posted_date_selector,
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
    for field_name in _expected_detail_fields(recipe):
        sources.setdefault(field_name, "detail")
    return sources


def _expected_detail_fields(recipe: JobBoardRecipe) -> list[str]:
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


def _selectable_soups(html: str) -> list[BeautifulSoup]:
    soups = [BeautifulSoup(html, "html.parser")]
    for fragment in _embedded_html_fragments(html):
        soups.append(BeautifulSoup(fragment, "html.parser"))
    return soups


def _embedded_html_fragments(html: str) -> list[str]:
    fragments: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type=lambda value: value and "json" in value):
        raw = script.string or script.get_text("", strip=True)
        if not raw or "<a" not in raw:
            continue
        fragments.extend(_strings_containing_links(raw))
    return fragments


def _strings_containing_links(raw: str) -> list[str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [raw] if "<a" in raw else []
    fragments: list[str] = []
    stack = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str) and "<a" in value:
            fragments.append(value)
    return fragments


def _looks_like_pagination(label: str, href: str, haystack: str) -> bool:
    if re.fullmatch(r"\d+", label.strip()):
        return True
    return any(token in haystack for token in ["page-numbers", "pagenr=", "pagination", "paginator", "/page/"])


def _looks_like_next_link(label: str, match: Tag) -> bool:
    classes = {str(item).lower() for item in match.get("class", [])}
    rel = match.get("rel") or []
    rel_values = {str(item).lower() for item in rel} if isinstance(rel, list) else {str(rel).lower()}
    haystack = " ".join([label, str(match.get("aria-label") or ""), str(match.get("href") or "")]).lower()
    return "next" in classes or "next" in rel_values or "next" in haystack


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


def _fetch_static_html(url: str, timeout_seconds: int) -> tuple[str, str, list[str]]:
    try:
        response = requests.get(
            url,
            timeout=timeout_seconds,
            headers={"User-Agent": "Job-Agent recipe tester (public page; low volume)"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(f"Fetch failed: {exc}") from exc
    return response.text, response.url, []


def _fetch_rendered_html(url: str, timeout_seconds: int) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise ValueError(
            "Rendered mode requested but Playwright is unavailable. "
            "Install requirements-playwright.txt and Chromium to use rendered_html recipes."
        ) from exc

    timeout_ms = timeout_seconds * 1000
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10_000))
                except PlaywrightError:
                    warnings.append("Rendered page did not become network-idle before the polite timeout.")
                return page.content(), page.url, warnings
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ValueError(f"Playwright render failed: {exc}") from exc


def _apply_detail_html(job: Job, html: str, recipe: JobBoardRecipe) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    schema_values = _extract_jobposting_json_ld(soup) if recipe.detail.use_json_ld else {}
    detail_root = soup.select_one(".job-single") or soup.body or soup
    pattern_values = _extract_pattern_values(detail_root.get_text(" ", strip=True), recipe.patterns)
    found_values = {
        "title": _select_text(soup, recipe.detail.title_selector)
        or schema_values.get("title", "")
        or pattern_values.get("title", ""),
        "description": _select_text(soup, recipe.detail.description_selector) or schema_values.get("description", ""),
        "location": _select_text(soup, recipe.detail.location_selector)
        or schema_values.get("location", "")
        or pattern_values.get("location", ""),
        "remote": _select_text(soup, recipe.detail.remote_selector)
        or schema_values.get("remote", "")
        or pattern_values.get("remote", ""),
        "rate": _select_text(soup, recipe.detail.rate_selector)
        or schema_values.get("rate", "")
        or pattern_values.get("rate", ""),
        "workload": _select_text(soup, recipe.detail.workload_selector)
        or schema_values.get("workload", "")
        or pattern_values.get("workload", "")
        or pattern_values.get("work_type", ""),
        "posted_date": _select_text(soup, recipe.detail.posted_date_selector)
        or schema_values.get("posted_date", "")
        or pattern_values.get("posted_date", ""),
        "start_date": _select_text(soup, recipe.detail.start_date_selector)
        or schema_values.get("start_date", "")
        or pattern_values.get("start_date", ""),
        "languages": _select_text(soup, recipe.detail.language_selector)
        or schema_values.get("language", "")
        or pattern_values.get("language", ""),
    }
    found_values = {field_name: value for field_name, value in found_values.items() if value}

    if found_values.get("title"):
        job.title = found_values["title"]
    if found_values.get("description"):
        cleaned_description = _html_to_text(found_values["description"])
        job.description = cleaned_description[:3000]
        job.raw_text = cleaned_description[:5000]
    if found_values.get("location") and job.location == "Not listed":
        job.location = found_values["location"]
    if found_values.get("remote") and job.remote == "Not listed":
        job.remote = found_values["remote"]
    if found_values.get("rate") and job.rate == "Not listed":
        job.rate = found_values["rate"]
    if found_values.get("workload") and job.workload == "Not listed":
        job.workload = found_values["workload"]
    if found_values.get("posted_date") and job.posted_date == "Not listed":
        job.posted_date = found_values["posted_date"]
        job.freshness_confidence = "recipe"
    if found_values.get("start_date") and job.start_date == "Not listed":
        job.start_date = found_values["start_date"]
    if found_values.get("languages") and not job.languages:
        job.languages = [found_values["languages"]]
    if found_values and "Detail page fetched by recipe; verify details manually." not in job.extraction_notes:
        job.extraction_notes.append("Detail page fetched by recipe; verify details manually.")
    return found_values


def _selector_fields(data: dict[str, Any], cls: type) -> dict[str, Any]:
    fields = cls.__dataclass_fields__
    values: dict[str, Any] = {}
    for key, field_info in fields.items():
        if key in data:
            if field_info.default is not MISSING and isinstance(field_info.default, bool):
                values[key] = bool(data[key])
            elif field_info.default is not MISSING and isinstance(field_info.default, int):
                values[key] = int(data[key])
            elif field_info.default is not MISSING and isinstance(field_info.default, float):
                values[key] = float(data[key])
            else:
                values[key] = _selector_value(data[key])
    return values


def _mapping_section(data: dict[str, Any], section: str) -> dict[str, Any]:
    value = data.get(section) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{section} must be a mapping.")
    return value


def _list_fields(data: dict[str, Any], cls: type, label: str) -> dict[str, Any]:
    values = {}
    for key in cls.__dataclass_fields__:
        if key in data:
            value = data.get(key) or []
            if not isinstance(value, list):
                raise ValueError(f"{label}.{key} must be a list.")
            values[key] = [str(item).strip() for item in value if str(item).strip()]
    return values


def _int_fields(data: dict[str, Any], cls: type) -> dict[str, Any]:
    values = {}
    for key in cls.__dataclass_fields__:
        if key in data:
            try:
                values[key] = int(data[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"limits.{key} must be an integer.") from exc
    return values


def _regex_fields(data: dict[str, Any], cls: type, label: str) -> dict[str, Any]:
    values = {}
    for key in cls.__dataclass_fields__:
        if key not in data:
            continue
        pattern = str(data.get(key) or "").strip()
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{label}: patterns.{key} is not a valid regex: {exc}") from exc
        values[key] = pattern
    return values


def _select_text(root: Tag, selector: SelectorValue) -> str:
    for css_selector in _selectors(selector):
        match = root if _matches_selector(root, css_selector) else root.select_one(css_selector)
        text = match.get_text(" ", strip=True) if match else ""
        if text:
            return text
    return ""


def _select_href(root: Tag, selector: SelectorValue) -> str:
    for css_selector in _selectors(selector):
        match = root if _matches_selector(root, css_selector) else root.select_one(css_selector)
        if not match:
            continue
        href = match.get("href")
        if href:
            return str(href).strip()
        nested = match.select_one("[href]")
        if nested and nested.get("href"):
            return str(nested.get("href", "")).strip()
    return ""


def _matches_selector(tag: Tag, selector: str) -> bool:
    selector = selector.strip()
    if selector == tag.name:
        return True
    if selector.startswith("."):
        return selector[1:] in tag.get("class", [])
    if selector.startswith("#"):
        return str(tag.get("id", "")) == selector[1:]
    if "." in selector and " " not in selector and ">" not in selector:
        tag_name, class_name = selector.split(".", 1)
        return tag.name == tag_name and class_name in tag.get("class", [])
    return False


def _should_reject(title: str, url: str, description: str, recipe: JobBoardRecipe) -> bool:
    normalized_title = " ".join(title.lower().split())
    if len(normalized_title) < recipe.limits.min_title_length:
        return True
    if title_quality(title) == "generic":
        return True
    if len(description.strip()) < recipe.limits.min_description_length:
        return True
    lowered_url = url.lower()
    if recipe.accept.url_contains and not any(fragment.lower() in lowered_url for fragment in recipe.accept.url_contains):
        return True
    if recipe.accept.title_contains and not any(
        fragment.lower() in normalized_title for fragment in recipe.accept.title_contains
    ):
        return True
    title_exact = {" ".join(item.lower().split()) for item in recipe.reject.title_exact}
    if normalized_title in title_exact:
        return True
    if any(fragment.lower() in normalized_title for fragment in recipe.reject.title_contains):
        return True
    return any(fragment.lower() in lowered_url for fragment in recipe.reject.url_contains)


def _extract_pattern_values(text: str, patterns: PatternsRecipe) -> dict[str, str]:
    values: dict[str, str] = {}
    pattern_map = {
        "title": patterns.title_regex,
        "job_id": patterns.job_id_regex,
        "location": patterns.location_regex,
        "remote": patterns.remote_regex,
        "rate": patterns.rate_regex,
        "workload": patterns.workload_regex,
        "posted_date": patterns.posted_date_regex,
        "start_date": patterns.start_date_regex,
        "language": patterns.language_regex,
        "work_type": patterns.work_type_regex,
    }
    for field_name, pattern in pattern_map.items():
        if not pattern:
            continue
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if not match:
            continue
        value = _regex_value(match, field_name)
        if value:
            values[field_name] = value
    return values


def _regex_value(match: re.Match[str], field_name: str) -> str:
    groups = match.groupdict()
    if groups.get(field_name):
        return _clean_pattern_value(groups[field_name])
    for value in groups.values():
        if value:
            return _clean_pattern_value(value)
    for value in match.groups():
        if value:
            return _clean_pattern_value(value)
    return _clean_pattern_value(match.group(0))


def _clean_pattern_value(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" :-\n\t")
    arrangement_labels = {
        "remote": "Remote",
        "hybrid": "Hybrid",
        "hybrid-remote": "Hybrid",
        "office based": "Office based",
    }
    return arrangement_labels.get(cleaned.lower(), cleaned)


def _extraction_notes(pattern_values: dict[str, str]) -> list[str]:
    notes = ["Recipe-based extraction; verify details manually."]
    if pattern_values.get("job_id"):
        notes.append(f"Recipe extracted job ID: {pattern_values['job_id']}")
    if pattern_values.get("work_type"):
        notes.append(f"Recipe extracted work type: {pattern_values['work_type']}")
    return notes


def _has_detail_selectors(recipe: JobBoardRecipe) -> bool:
    if recipe.detail.use_json_ld:
        return True
    if any(
        pattern
        for pattern in [
            recipe.patterns.title_regex,
            recipe.patterns.location_regex,
            recipe.patterns.remote_regex,
            recipe.patterns.rate_regex,
            recipe.patterns.workload_regex,
            recipe.patterns.posted_date_regex,
            recipe.patterns.start_date_regex,
            recipe.patterns.language_regex,
            recipe.patterns.work_type_regex,
        ]
    ):
        return True
    return any(
        _selectors(selector)
        for selector in [
            recipe.detail.description_selector,
            recipe.detail.title_selector,
            recipe.detail.location_selector,
            recipe.detail.remote_selector,
            recipe.detail.rate_selector,
            recipe.detail.workload_selector,
            recipe.detail.posted_date_selector,
            recipe.detail.start_date_selector,
            recipe.detail.language_selector,
        ]
    )


def _selector_value(value: Any) -> SelectorValue:
    if isinstance(value, list):
        selectors = [str(item).strip() for item in value if str(item).strip()]
        if not selectors:
            return ""
        return selectors
    if value is None:
        return ""
    return str(value).strip()


def _selectors(value: SelectorValue) -> list[str]:
    if isinstance(value, list):
        return [selector for selector in value if selector]
    return [value] if value else []


def _has_selector_value(value: Any) -> bool:
    return bool(_selectors(_selector_value(value)))


def _validate_positive_int(value: int, field_name: str, label: str) -> None:
    if value <= 0:
        raise ValueError(f"{label}: {field_name} must be a positive integer.")


def _selected_urls(soup: BeautifulSoup, selector: SelectorValue, base_url: str) -> set[str]:
    urls: set[str] = set()
    for css_selector in _selectors(selector):
        for match in soup.select(css_selector):
            href = match.get("href")
            if href:
                urls.add(urljoin(base_url, str(href).strip()))
    return urls


def _canonical_url(soup: BeautifulSoup, base_url: str) -> str:
    canonical = soup.select_one('link[rel="canonical"]')
    if canonical and canonical.get("href"):
        return urljoin(base_url, str(canonical.get("href", "")).strip())
    return base_url


def _extract_jobposting_json_ld(soup: BeautifulSoup) -> dict[str, str]:
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text("", strip=True)
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            fallback = _extract_loose_jobposting_json(raw)
            if fallback:
                return fallback
            continue
        posting = _find_jobposting(data)
        if not posting:
            continue
        return {
            "title": _json_text(posting.get("title")),
            "description": _html_to_text(_json_text(posting.get("description"))),
            "posted_date": _json_text(posting.get("datePosted")),
            "workload": _employment_type(posting.get("employmentType")),
            "location": _job_location(posting.get("jobLocation")),
            "rate": _base_salary(posting.get("baseSalary")),
        }
    return {}


def _extract_loose_jobposting_json(raw: str) -> dict[str, str]:
    if "JobPosting" not in raw:
        return {}
    return {
        "title": _loose_json_string(raw, "title"),
        "description": _html_to_text(_loose_json_string(raw, "description")),
        "posted_date": _loose_json_string(raw, "datePosted"),
        "workload": _employment_type(_loose_json_string(raw, "employmentType")),
        "location": _loose_json_string(raw, "addressCountry"),
        "rate": _loose_base_salary(raw),
    }


def _find_jobposting(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        item_type = value.get("@type")
        item_types = item_type if isinstance(item_type, list) else [item_type]
        if any(str(item).lower() == "jobposting" for item in item_types):
            return value
        graph_match = _find_jobposting(value.get("@graph"))
        if graph_match:
            return graph_match
        for child in value.values():
            child_match = _find_jobposting(child)
            if child_match:
                return child_match
    if isinstance(value, list):
        for item in value:
            item_match = _find_jobposting(item)
            if item_match:
                return item_match
    return None


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(text for item in value if (text := _json_text(item)))
    if isinstance(value, dict):
        return ""
    return str(value).strip()


def _loose_json_string(raw: str, key: str) -> str:
    pattern = rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"])*)"'
    match = re.search(pattern, raw, flags=re.DOTALL)
    if not match:
        return ""
    value = match.group(1).replace("\\/", "/").replace('\\"', '"')
    return re.sub(r"\s+", " ", value).strip()


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _employment_type(value: Any) -> str:
    text = _json_text(value)
    if text:
        return text.replace("_", " ").title()
    return ""


def _job_location(value: Any) -> str:
    locations = value if isinstance(value, list) else [value]
    parts: list[str] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if isinstance(address, dict):
            location_parts = [
                _json_text(address.get("addressLocality")),
                _json_text(address.get("addressRegion")),
                _json_text(address.get("addressCountry")),
            ]
            text = ", ".join(part for part in location_parts if part)
            if text:
                parts.append(text)
        elif address:
            parts.append(_json_text(address))
    return "; ".join(parts)


def _base_salary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    currency = _json_text(value.get("currency"))
    salary_value = value.get("value")
    amount = ""
    unit = ""
    if isinstance(salary_value, dict):
        amount = _json_text(salary_value.get("value"))
        unit = _json_text(salary_value.get("unitText"))
        min_value = _json_text(salary_value.get("minValue"))
        max_value = _json_text(salary_value.get("maxValue"))
        if not amount and (min_value or max_value):
            amount = f"{min_value}-{max_value}".strip("-")
    else:
        amount = _json_text(salary_value)
    if not amount:
        return ""
    return " ".join(part for part in [currency, amount, unit] if part)


def _loose_base_salary(raw: str) -> str:
    currency = _loose_json_string(raw, "currency")
    unit = _loose_json_string(raw, "unitText")
    value = _loose_json_string(raw, "value")
    if not value:
        return ""
    return " ".join(part for part in [currency, value, unit] if part)
