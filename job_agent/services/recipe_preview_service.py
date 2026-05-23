from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from job_agent.models import Job
from job_agent.services.extraction_quality import MIN_USEFUL_DESCRIPTION_CHARS
from job_agent.services.job_board_recipe_service import (
    ApplicationEntry,
    DetailPageAttempt,
    PaginationLink,
    RecipeCapabilityCheck,
    RecipeExtractionResult,
    RecipeFieldCheck,
    extract_job_detail_from_html,
    extract_jobs_with_recipe_from_html,
    extract_jobs_with_recipe_from_url,
    load_job_board_recipe,
    quality_from_recipe_result,
)


@dataclass
class PreviewJob:
    title: str
    url: str
    location: str
    remote: str
    rate: str
    workload: str
    posted_date: str
    start_date: str
    languages: list[str]
    description_preview: str
    extraction_notes: list[str]


@dataclass
class FieldCoverage:
    field: str
    present_count: int
    total_count: int

    @property
    def label(self) -> str:
        return self.field.replace("_", " ").title()


@dataclass
class PreviewRunStep:
    phase: str
    status: str
    detail: str


@dataclass
class RecipePreviewResult:
    recipe_source_name: str
    recipe_path: str
    recipe_status: str
    input_type: str
    input_value: str
    base_url: str
    mode_used: str
    extracted_job_count: int
    useful_titles: int
    generic_labels: int
    unique_urls: int
    average_description_length: int
    jobs: list[PreviewJob] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    field_coverage: list[FieldCoverage] = field(default_factory=list)
    pagination_configured: bool = False
    pagination_link_count: int = 0
    pagination_max_pages: int = 1
    pagination_links: list[PaginationLink] = field(default_factory=list)
    pagination_fetch_count: int = 0
    listing_observed_count: int = 0
    listing_extracted_count: int = 0
    listing_missing_url_count: int = 0
    listing_rejected_count: int = 0
    listing_duplicate_count: int = 0
    listing_limit_skipped_count: int = 0
    listing_pages: list = field(default_factory=list)
    count_explanations: list[str] = field(default_factory=list)
    detail_follow_enabled: bool = False
    detail_max_pages: int = 0
    detail_request_delay_seconds: float = 0.0
    detail_fetch_count: int = 0
    detail_enriched_count: int = 0
    detail_fetch_limit: int | None = None
    detail_sample_input: str = ""
    detail_sample: PreviewJob | None = None
    detail_field_coverage: list[FieldCoverage] = field(default_factory=list)
    request_notes: list[str] = field(default_factory=list)
    run_steps: list[PreviewRunStep] = field(default_factory=list)
    field_checks: list[RecipeFieldCheck] = field(default_factory=list)
    capability_checks: list[RecipeCapabilityCheck] = field(default_factory=list)
    detail_attempts: list[DetailPageAttempt] = field(default_factory=list)
    application_entries: list[ApplicationEntry] = field(default_factory=list)


@dataclass
class RecipeExplanationItem:
    label: str
    detail: str


@dataclass
class RecipeExplanation:
    source_name: str
    status: str
    start_url: str
    mode_label: str
    max_cards: int
    detail_follow: bool
    detail_max_pages: int
    detail_delay: float
    pagination_configured: bool
    pagination_max_pages: int
    listing_fields: list[RecipeExplanationItem]
    navigation_fields: list[RecipeExplanationItem]
    filter_notes: list[str]


def explain_recipe(recipe_path: str | Path, root: Path | None = None) -> RecipeExplanation | None:
    if not str(recipe_path).strip():
        return None

    root = root or Path.cwd()
    resolved_recipe_path = _resolve_path(recipe_path, root)
    try:
        recipe = load_job_board_recipe(resolved_recipe_path)
    except (OSError, ValueError):
        return None
    listing_fields = [
        RecipeExplanationItem("Job card", _selector_detail(recipe.listing.card_selector)),
        RecipeExplanationItem("Title", _selector_detail(recipe.listing.title_selector)),
        RecipeExplanationItem("Detail URL", _selector_detail(recipe.listing.link_selector)),
        RecipeExplanationItem("Location", _selector_detail(recipe.listing.location_selector)),
        RecipeExplanationItem("Remote", _selector_detail(recipe.listing.remote_selector)),
        RecipeExplanationItem("Rate", _selector_detail(recipe.listing.rate_selector)),
        RecipeExplanationItem("Workload", _selector_detail(recipe.listing.workload_selector)),
        RecipeExplanationItem("Posted date", _selector_detail(recipe.listing.posted_date_selector)),
        RecipeExplanationItem("Start date", _selector_detail(recipe.listing.start_date_selector)),
        RecipeExplanationItem("Description", _selector_detail(recipe.listing.description_selector)),
    ]
    navigation_fields = [
        RecipeExplanationItem(
            "Detail pages",
            (
                "Actual source runs follow every retained listing URL; preview and compatibility checks sample one "
                f"detail URL with {recipe.detail.request_delay_seconds:g}s delay."
                if recipe.detail.follow
                else "Does not fetch job detail pages."
            ),
        ),
        RecipeExplanationItem("Detail title", _selector_detail(recipe.detail.title_selector)),
        RecipeExplanationItem("Detail description", _selector_detail(recipe.detail.description_selector)),
        RecipeExplanationItem("Detail structured data", "Uses JobPosting JSON-LD." if recipe.detail.use_json_ld else "Not configured."),
        RecipeExplanationItem(
            "Pagination links",
            (
                f"{_selector_detail(recipe.pagination.page_link_selector)}; actual runs may follow up to "
                f"{recipe.pagination.max_pages} pages, proof runs follow one additional page."
                if recipe.pagination.page_link_selector
                else "Not configured."
            ),
        ),
        RecipeExplanationItem("Next-page link", _selector_detail(recipe.pagination.next_selector)),
    ]
    return RecipeExplanation(
        source_name=recipe.source_name,
        status=_recipe_status(resolved_recipe_path, recipe.source_name),
        start_url=recipe.start_url,
        mode_label="Rendered page" if recipe.mode == "rendered_html" else "Static HTML",
        max_cards=recipe.limits.max_cards,
        detail_follow=recipe.detail.follow,
        detail_max_pages=recipe.detail.max_detail_pages,
        detail_delay=recipe.detail.request_delay_seconds,
        pagination_configured=bool(recipe.pagination.page_link_selector or recipe.pagination.next_selector),
        pagination_max_pages=recipe.pagination.max_pages,
        listing_fields=[item for item in listing_fields if item.detail != "Not configured."],
        navigation_fields=navigation_fields,
        filter_notes=_filter_notes(recipe),
    )


def preview_recipe(
    recipe_path: str | Path,
    input_value: str | Path,
    base_url: str = "",
    rendered: bool = False,
    static: bool = False,
    detail_input_value: str | Path = "",
    root: Path | None = None,
) -> RecipePreviewResult:
    if rendered and static:
        raise ValueError("Use either --rendered or --static, not both.")

    root = root or Path.cwd()
    resolved_recipe_path = _resolve_path(recipe_path, root)
    recipe = load_job_board_recipe(resolved_recipe_path)
    extraction = _run_preview_extraction(recipe, input_value, base_url=base_url, rendered=rendered, static=static, root=root)
    quality = quality_from_recipe_result(extraction, recipe)
    warnings = list(quality.warnings)
    input_type = _input_type(str(input_value))
    field_coverage = _field_coverage(extraction.jobs)
    detail_sample = None
    detail_field_coverage: list[FieldCoverage] = []
    detail_value = str(detail_input_value).strip()
    if detail_value:
        detail_job = _run_detail_sample(recipe, detail_value, base_url=extraction.base_url, root=root)
        detail_sample = _preview_job(detail_job)
        detail_field_coverage = _field_coverage([detail_job])
        if not recipe.detail.follow:
            warnings.append("Recipe parsed the detail sample, but detail.follow is false; URL runs will not fetch job detail pages.")

    return RecipePreviewResult(
        recipe_source_name=recipe.source_name,
        recipe_path=str(resolved_recipe_path),
        recipe_status=_recipe_status(resolved_recipe_path, recipe.source_name),
        input_type=input_type,
        input_value=str(input_value),
        base_url=extraction.base_url,
        mode_used=extraction.mode_used,
        extracted_job_count=quality.candidate_count,
        useful_titles=quality.useful_title_count,
        generic_labels=quality.generic_title_count,
        unique_urls=quality.unique_url_count,
        average_description_length=quality.average_description_length,
        jobs=[_preview_job(job) for job in extraction.jobs],
        warnings=warnings,
        field_coverage=field_coverage,
        pagination_configured=bool(recipe.pagination.page_link_selector or recipe.pagination.next_selector),
        pagination_link_count=len(extraction.pagination_links),
        pagination_max_pages=recipe.pagination.max_pages,
        pagination_links=extraction.pagination_links,
        pagination_fetch_count=extraction.pagination_fetch_count,
        listing_observed_count=extraction.listing_observed_count,
        listing_extracted_count=extraction.listing_extracted_count,
        listing_missing_url_count=extraction.listing_missing_url_count,
        listing_rejected_count=extraction.listing_rejected_count,
        listing_duplicate_count=extraction.listing_duplicate_count,
        listing_limit_skipped_count=extraction.listing_limit_skipped_count,
        listing_pages=extraction.listing_pages,
        count_explanations=_preview_count_explanations(extraction),
        detail_follow_enabled=recipe.detail.follow,
        detail_max_pages=recipe.detail.max_detail_pages,
        detail_request_delay_seconds=recipe.detail.request_delay_seconds,
        detail_fetch_count=extraction.detail_fetch_count,
        detail_enriched_count=extraction.detail_enriched_count,
        detail_fetch_limit=extraction.detail_fetch_limit,
        detail_sample_input=detail_value,
        detail_sample=detail_sample,
        detail_field_coverage=detail_field_coverage,
        request_notes=_request_notes(recipe, input_type, bool(detail_value)),
        run_steps=_run_steps(recipe, str(input_value), input_type, extraction, quality, field_coverage, warnings),
        field_checks=extraction.field_checks,
        capability_checks=extraction.capability_checks,
        detail_attempts=extraction.detail_attempts,
        application_entries=extraction.application_entries,
    )


def _run_preview_extraction(
    recipe,
    input_value: str | Path,
    base_url: str,
    rendered: bool,
    static: bool,
    root: Path,
) -> RecipeExtractionResult:
    value = str(input_value).strip()
    if value.startswith(("http://", "https://")):
        forced_rendered = True if rendered else False if static else None
        return extract_jobs_with_recipe_from_url(
            value,
            recipe,
            rendered=forced_rendered,
            use_recipe_detail_limit=False,
            detail_page_limit=1,
            fetch_pagination=True,
            pagination_page_limit=2,
        )

    if rendered:
        raise ValueError("--rendered can only be used with a public http(s) URL.")
    resolved_base_url = base_url.strip() or recipe.start_url.strip()
    if not resolved_base_url:
        raise ValueError("Testing a local HTML file requires --base-url or recipe.start_url.")

    path = _resolve_path(value, root)
    if not path.exists():
        raise ValueError(f"HTML fixture not found: {path}")

    warnings = []
    if recipe.mode == "rendered_html":
        warnings.append("Local fixture HTML ignores recipe mode: rendered_html.")
    html = path.read_text(encoding="utf-8")
    return extract_jobs_with_recipe_from_html(
        html,
        base_url=resolved_base_url,
        recipe=recipe,
        mode_used="local_fixture_html",
        warnings=warnings,
    )


def _run_detail_sample(recipe, input_value: str, base_url: str, root: Path) -> Job:
    if input_value.startswith(("http://", "https://")):
        raise ValueError("Detail sample preview currently accepts a local HTML file, not a public URL.")
    path = _resolve_path(input_value, root)
    if not path.exists():
        raise ValueError(f"Detail HTML sample not found: {path}")
    html = path.read_text(encoding="utf-8")
    return extract_job_detail_from_html(html, base_url=base_url or recipe.start_url, recipe=recipe)


def _preview_job(job: Job) -> PreviewJob:
    return PreviewJob(
        title=job.title,
        url=job.url,
        location=job.location,
        remote=job.remote,
        rate=job.rate,
        workload=job.workload,
        posted_date=job.posted_date,
        start_date=job.start_date,
        languages=list(job.languages),
        description_preview=_description_preview(job.description),
        extraction_notes=list(job.extraction_notes),
    )


def _description_preview(description: str, limit: int = 280) -> str:
    text = " ".join(description.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


REPORT_FIELDS = [
    "title",
    "url",
    "location",
    "remote",
    "rate",
    "workload",
    "posted_date",
    "start_date",
    "languages",
    "description",
]


def _field_coverage(jobs: list[Job]) -> list[FieldCoverage]:
    return [
        FieldCoverage(field=field, present_count=sum(1 for job in jobs if _field_present(job, field)), total_count=len(jobs))
        for field in REPORT_FIELDS
    ]


def _field_present(job: Job, field: str) -> bool:
    value = getattr(job, field)
    if isinstance(value, list):
        return bool(value)
    if field == "description":
        return len(str(value).strip()) >= MIN_USEFUL_DESCRIPTION_CHARS
    return bool(str(value).strip()) and str(value).strip() != "Not listed"


def _request_notes(recipe, input_type: str, has_detail_sample: bool) -> list[str]:
    notes: list[str] = []
    if input_type == "public URL":
        notes.append("Preview is a proof run: it fetches the provided listing URL and only enough follow-up pages to prove configured navigation.")
        if recipe.detail.follow:
            notes.append(
                "Detail proof is bounded to 1 listing URL"
                f" with {recipe.detail.request_delay_seconds:g}s delay between requests."
            )
        else:
            notes.append("Recipe detail.follow is false, so URL preview will not fetch job detail pages.")
    else:
        notes.append("Local HTML preview made no network requests.")
    if recipe.pagination.page_link_selector or recipe.pagination.next_selector:
        notes.append(
            "Pagination proof follows at most 1 additional page; actual source runs may follow configured pagination "
            f"up to {recipe.pagination.max_pages} pages."
        )
    notes.append("Actual configured source runs enrich all retained jobs, not the preview detail proof sample.")
    if has_detail_sample:
        notes.append("Detail sample extraction used the provided local detail HTML only.")
    return notes


def _run_steps(
    recipe,
    input_value: str,
    input_type: str,
    extraction: RecipeExtractionResult,
    quality,
    field_coverage: list[FieldCoverage],
    warnings: list[str],
) -> list[PreviewRunStep]:
    steps = [
        PreviewRunStep(
            phase="Source resolved",
            status="completed",
            detail=f"Input was treated as {input_type}: {input_value}. Base URL resolved to {extraction.base_url}.",
        )
    ]
    steps.extend(
        PreviewRunStep(phase=step.phase, status=step.status, detail=step.detail)
        for step in extraction.steps
    )
    steps.extend(
        [
            PreviewRunStep(
                phase="Report field coverage",
                status="completed" if any(field.present_count for field in field_coverage) else "warning",
                detail=_coverage_sentence(field_coverage),
            ),
            PreviewRunStep(
                phase="Capability checks",
                status="completed" if not any(check.status == "fail" for check in extraction.capability_checks) else "warning",
                detail=_capability_sentence(extraction.capability_checks),
            ),
            PreviewRunStep(
                phase="Limits and filters",
                status="completed",
                detail=(
                    f"Read at most {recipe.limits.max_cards} cards per listing page, required title length "
                    f"{recipe.limits.min_title_length}, and applied configured accept/reject filters."
                ),
            ),
        ]
    )
    if warnings:
        steps.append(
            PreviewRunStep(
                phase="Warnings",
                status="warning",
                detail="; ".join(warnings),
            )
        )
    return steps


def _coverage_sentence(field_coverage: list[FieldCoverage]) -> str:
    if not field_coverage:
        return "No jobs were available for field coverage checks."
    interesting = [
        f"{field.label}: {field.present_count}/{field.total_count}"
        for field in field_coverage
        if field.field in {"title", "url", "location", "rate", "workload", "description"}
    ]
    return "; ".join(interesting) + "."


def _preview_count_explanations(extraction: RecipeExtractionResult) -> list[str]:
    explanations: list[str] = []
    observed = extraction.listing_observed_count
    retained = len(extraction.jobs)
    if observed:
        if observed == retained and not any(
            [
                extraction.listing_missing_url_count,
                extraction.listing_rejected_count,
                extraction.listing_duplicate_count,
                extraction.listing_limit_skipped_count,
            ]
        ):
            explanations.append(f"Observed {observed} listing card(s) and retained all {retained} as jobs.")
        else:
            reasons = []
            if extraction.listing_missing_url_count:
                reasons.append(f"{extraction.listing_missing_url_count} card(s) had no recipe-readable job URL")
            if extraction.listing_rejected_count:
                reasons.append(f"{extraction.listing_rejected_count} card(s) were rejected by recipe filters")
            if extraction.listing_duplicate_count:
                reasons.append(f"{extraction.listing_duplicate_count} duplicate URL(s) were ignored")
            if extraction.listing_limit_skipped_count:
                reasons.append(f"{extraction.listing_limit_skipped_count} card(s) were outside the configured run limit")
            reason_text = "; ".join(reasons) if reasons else "some cards did not produce retained jobs"
            explanations.append(f"Observed {observed} listing card(s) and retained {retained} job(s): {reason_text}.")
    return explanations


def _capability_sentence(checks) -> str:
    if not checks:
        return "No capability checks were recorded."
    return "; ".join(f"{check.label}: {check.status}" for check in checks) + "."


def _pagination_step_detail(recipe, extraction: RecipeExtractionResult) -> str:
    if not (recipe.pagination.page_link_selector or recipe.pagination.next_selector):
        return "Recipe has no pagination selectors configured."
    return (
        f"Looked for pagination with selectors {_selector_detail(recipe.pagination.page_link_selector)}"
        f" and next selector {_selector_detail(recipe.pagination.next_selector)}. Found "
        f"{len(extraction.pagination_links)} links. Preview does not fetch extra pagination pages; recipe cap is "
        f"{recipe.pagination.max_pages} pages."
    )


def _detail_step_detail(recipe, extraction: RecipeExtractionResult) -> str:
    if not recipe.detail.follow:
        return "Recipe detail.follow is false, so no job-specific detail pages were requested."
    enriched_count = _detail_enriched_count(extraction.jobs)
    return (
        f"Recipe detail.follow is true, capped at {recipe.detail.max_detail_pages} detail URLs with "
        f"{recipe.detail.request_delay_seconds:g}s delay. {enriched_count} jobs show detail-page enrichment notes."
    )


def _detail_enriched_count(jobs: list[Job]) -> int:
    return sum(
        1
        for job in jobs
        if any("Detail page fetched" in note or "Detail page sample" in note for note in job.extraction_notes)
    )


def _selector_detail(value: object) -> str:
    if value is None:
        return "Not configured."
    if isinstance(value, list):
        selectors = [str(item).strip() for item in value if str(item).strip()]
    else:
        selectors = [str(value).strip()] if str(value).strip() else []
    return ", ".join(selectors) if selectors else "Not configured."


def _filter_notes(recipe) -> list[str]:
    notes = [
        f"Reads at most {recipe.limits.max_cards} listing cards from the provided page.",
        f"Requires titles to be at least {recipe.limits.min_title_length} characters.",
    ]
    if recipe.limits.min_description_length:
        notes.append(f"Requires descriptions to be at least {recipe.limits.min_description_length} characters.")
    if recipe.accept.url_contains:
        notes.append("Accepts URLs containing: " + ", ".join(recipe.accept.url_contains) + ".")
    if recipe.accept.title_contains:
        notes.append("Accepts titles containing: " + ", ".join(recipe.accept.title_contains) + ".")
    if recipe.reject.title_exact:
        notes.append("Rejects exact titles such as: " + ", ".join(recipe.reject.title_exact[:8]) + ".")
    if recipe.reject.title_contains:
        notes.append("Rejects titles containing: " + ", ".join(recipe.reject.title_contains[:8]) + ".")
    if recipe.reject.url_contains:
        notes.append("Rejects URLs containing: " + ", ".join(recipe.reject.url_contains[:8]) + ".")
    if recipe.patterns.job_id_regex:
        notes.append("Extracts job IDs with a configured pattern.")
    return notes


def _input_type(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return "public URL"
    normalized = value.replace("\\", "/").lower()
    if "output/recipe-calibration/" in normalized:
        return "local artifact"
    if "tests/fixtures/" in normalized:
        return "local fixture"
    return "local artifact"


def _recipe_status(recipe_path: Path, source_name: str) -> str:
    marker = str(recipe_path).replace("\\", "/").lower()
    if "/experimental/" in marker or "experimental" in source_name.lower():
        return "experimental"
    return "unspecified"


def _resolve_path(value: str | Path, root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path
