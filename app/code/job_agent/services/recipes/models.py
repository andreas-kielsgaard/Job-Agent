from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from job_agent.models import Job

SelectorValue = str | list[str]
VALID_MODES = {"static_html", "rendered_html"}
VALID_PAGINATION_STRATEGIES = {"url", "ajax", "browser_click"}
VALID_API_METHODS = {"GET", "POST"}
VALID_API_PAGINATION_STRATEGIES = {"none", "page", "offset"}


@dataclass
class ListingRecipe:
    card_selector: str = ""
    title_selector: SelectorValue = ""
    link_selector: SelectorValue = ""
    company_selector: SelectorValue = ""
    location_selector: SelectorValue = ""
    remote_selector: SelectorValue = ""
    rate_selector: SelectorValue = ""
    workload_selector: SelectorValue = ""
    posted_date_selector: SelectorValue = ""
    start_date_selector: SelectorValue = ""
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
    strategy: str = "url"
    page_link_selector: SelectorValue = ""
    next_selector: SelectorValue = ""
    click_selector: SelectorValue = ""
    ajax_url_template: str = ""
    max_pages: int = 1
    request_delay_seconds: float = 1.0


@dataclass
class ApiFieldMapping:
    title: str = ""
    url: str = ""
    url_template: str = ""
    application_url: str = ""
    company: str = ""
    recruiter: str = ""
    end_client: str = ""
    location: str = ""
    remote: str = ""
    rate: str = ""
    workload: str = ""
    contract_duration: str = ""
    posted_date: str = ""
    start_date: str = ""
    deadline: str = ""
    languages: str = ""
    description: str = ""
    description_html: str = ""
    raw_text: str = ""
    job_id: str = ""


@dataclass
class ApiPaginationRecipe:
    strategy: str = "none"
    page_param: str = ""
    page_start: int = 1
    offset_param: str = ""
    offset_start: int = 0
    page_size_param: str = ""
    page_size: int = 0
    max_pages: int = 1
    request_delay_seconds: float = 1.0


@dataclass
class ApiRequestRecipe:
    method: str = "GET"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    results_path: str = ""
    total_path: str = ""
    fields: ApiFieldMapping = field(default_factory=ApiFieldMapping)
    pagination: ApiPaginationRecipe = field(default_factory=ApiPaginationRecipe)


@dataclass
class AccessRecipe:
    requires_session: bool = False
    session_scope: str = ""
    setup_hint: str = ""


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
    page_explored_count: int = 0
    page_total: int = 0
    jobs_found: int = 0


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
            "api_listing": "API listing",
            "listing_total_access": "Listing total access",
            "job_urls": "Job URLs",
            "pagination_detection": "Pagination detection",
            "pagination_strategy": "Pagination strategy",
            "pagination_navigation": "Pagination navigation",
            "ajax_pagination": "AJAX pagination",
            "api_pagination": "API pagination",
            "browser_click_pagination": "Browser-click pagination",
            "pagination_duplicate_pages": "Duplicate pagination pages",
            "source_access": "Source access",
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
    listing: ListingRecipe = field(default_factory=ListingRecipe)
    start_url: str = ""
    mode: str = "static_html"
    access: AccessRecipe = field(default_factory=AccessRecipe)
    accept: AcceptRecipe = field(default_factory=AcceptRecipe)
    listing_api: ApiRequestRecipe = field(default_factory=ApiRequestRecipe)
    detail_api: ApiRequestRecipe = field(default_factory=ApiRequestRecipe)
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
    detail_listing_page_sample_target: int = 0
    detail_verified_listing_page_count: int = 0
    pagination_fetch_count: int = 0
    pagination_fetch_attempts: list[str] = field(default_factory=list)
    source_access_session_used: bool = False
    source_access_login_gate_detected: bool = False
    listing_pages: list[ListingExtractionStats] = field(default_factory=list)
    listing_observed_count: int = 0
    listing_extracted_count: int = 0
    listing_missing_url_count: int = 0
    listing_rejected_count: int = 0
    listing_duplicate_count: int = 0
    listing_limit_skipped_count: int = 0
    visible_total_job_count: int = 0
    pagination_duplicate_page_count: int = 0
    pagination_duplicate_ratio: float = 0.0
    pagination_unique_jobs_from_fetched_pages: int = 0
    pagination_strategy_used: str = ""
    interactive_pagination_control_count: int = 0
    access_strategy: str = "html"
    api_request_count: int = 0
    records_observed_count: int = 0
    json_records_extracted_count: int = 0
