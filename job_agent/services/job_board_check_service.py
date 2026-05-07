from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any
from urllib.parse import urlparse

import requests

from job_agent.models import Job
from job_agent.sources import extract_generic_jobs_from_html

PUBLIC_URL_MESSAGE = (
    "Only public http(s) job-board or recruiter pages are supported. "
    "Do not use login, session, captcha, private-network, or protected URLs."
)

GENERIC_TITLE_LABELS = {
    "apply",
    "apply now",
    "apply today",
    "details",
    "learn more",
    "more",
    "more info",
    "read more",
    "see details",
    "view",
    "view details",
    "view job",
    "view role",
}

MIN_USEFUL_DESCRIPTION_CHARS = 80


@dataclass
class CandidateQuality:
    title: str
    url: str
    title_quality: str
    description_length: int
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class ExtractionQuality:
    label: str
    status_code: int | None = None
    final_url: str = ""
    visible_text_chars: int = 0
    candidates: list[CandidateQuality] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def useful_title_count(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.title_quality == "useful")

    @property
    def generic_title_count(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.title_quality == "generic")

    @property
    def unique_url_count(self) -> int:
        return len({candidate.url for candidate in self.candidates if candidate.url})

    @property
    def average_description_length(self) -> int:
        if not self.candidates:
            return 0
        return round(mean(candidate.description_length for candidate in self.candidates))


@dataclass
class CompatibilityReport:
    url: str
    normal_html: ExtractionQuality
    rendered_page: ExtractionQuality | None
    recommendation: str
    recommendation_reason: str
    boundaries: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "normal_html": _quality_as_dict(self.normal_html),
            "rendered_page": _quality_as_dict(self.rendered_page) if self.rendered_page else None,
            "recommendation": self.recommendation,
            "recommendation_reason": self.recommendation_reason,
            "boundaries": self.boundaries,
        }


def check_job_board_compatibility(url: str, render: bool = True, timeout_seconds: int = 15) -> CompatibilityReport:
    normalized_url = validate_public_url(url)
    normal_html = _extract_from_http(normalized_url, timeout_seconds=timeout_seconds)
    rendered_page = _extract_from_playwright(normalized_url, timeout_seconds=timeout_seconds) if render else None
    recommendation, reason = _recommend(normal_html, rendered_page)
    return CompatibilityReport(
        url=normalized_url,
        normal_html=normal_html,
        rendered_page=rendered_page,
        recommendation=recommendation,
        recommendation_reason=reason,
        boundaries=[
            "Fetched only the provided URL with a polite timeout.",
            "No login, session, cookie, captcha, bot-protection bypass, endpoint discovery, or site scanning.",
            "Playwright rendering, when enabled, navigates only to the same provided page.",
        ],
    )


def validate_public_url(url: str) -> str:
    value = url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(PUBLIC_URL_MESSAGE)
    hostname = (parsed.hostname or "").lower()
    if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".local"):
        raise ValueError(PUBLIC_URL_MESSAGE)
    return value


def _extract_from_http(url: str, timeout_seconds: int) -> ExtractionQuality:
    quality = ExtractionQuality(label="Normal HTML")
    try:
        response = requests.get(
            url,
            timeout=timeout_seconds,
            headers={"User-Agent": "Job-Agent compatibility checker (public page; low volume)"},
        )
        quality.status_code = response.status_code
        quality.final_url = response.url
        response.raise_for_status()
    except requests.RequestException as exc:
        quality.warnings.append(f"Fetch failed: {exc}")
        return quality

    jobs = extract_generic_jobs_from_html(response.text, base_url=response.url, source_name="Compatibility check")
    quality.candidates = [_candidate_quality(job) for job in jobs]
    if not jobs:
        quality.warnings.append("No plausible job links were found in the initial HTML.")
    return quality


def _extract_from_playwright(url: str, timeout_seconds: int) -> ExtractionQuality:
    quality = ExtractionQuality(label="Playwright-rendered page")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        quality.warnings.append(
            "Playwright is not installed. Install requirements-playwright.txt and Chromium to compare rendered content."
        )
        return quality

    timeout_ms = timeout_seconds * 1000
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                quality.status_code = response.status if response else None
                quality.final_url = page.url
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10_000))
                except PlaywrightError:
                    quality.warnings.append("Rendered page did not become network-idle before the polite timeout.")
                html = page.content()
                body = page.locator("body")
                quality.visible_text_chars = len(body.inner_text(timeout=5_000)) if body.count() else 0
            finally:
                browser.close()
    except PlaywrightError as exc:
        quality.warnings.append(f"Playwright render failed: {exc}")
        return quality

    jobs = extract_generic_jobs_from_html(html, base_url=quality.final_url or url, source_name="Compatibility check")
    quality.candidates = [_candidate_quality(job) for job in jobs]
    if not jobs:
        quality.warnings.append("No plausible job links were found after rendering the same page.")
    return quality


def _candidate_quality(job: Job) -> CandidateQuality:
    missing_fields = []
    if not job.title.strip():
        missing_fields.append("title")
    if not job.url.strip():
        missing_fields.append("url")
    if job.company == "Unknown":
        missing_fields.append("company")
    if job.location == "Not listed":
        missing_fields.append("location")
    if job.posted_date == "Not listed":
        missing_fields.append("posted_date")
    description_length = len(job.description.strip())
    if description_length < MIN_USEFUL_DESCRIPTION_CHARS:
        missing_fields.append("description")
    return CandidateQuality(
        title=job.title,
        url=job.url,
        title_quality=_title_quality(job.title),
        description_length=description_length,
        missing_fields=missing_fields,
    )


def _title_quality(title: str) -> str:
    normalized = " ".join(title.lower().split())
    if normalized in GENERIC_TITLE_LABELS:
        return "generic"
    if len(normalized) < 8:
        return "generic"
    return "useful"


def _recommend(
    normal_html: ExtractionQuality,
    rendered_page: ExtractionQuality | None,
) -> tuple[str, str]:
    if _is_enough(normal_html):
        return (
            "current generic extractor is enough",
            "The initial HTML produced useful job titles, unique URLs, and usable surrounding description text.",
        )
    if rendered_page and _is_enough(rendered_page):
        return (
            "later extraction recipe may be useful",
            "The rendered page produced a materially better candidate set than the initial HTML.",
        )
    best = max([quality for quality in [normal_html, rendered_page] if quality], key=lambda item: item.useful_title_count)
    if best.candidate_count:
        return (
            "manual intake recommended",
            "The checker found candidate links, but titles or description context are too thin for reliable generic import.",
        )
    return (
        "manual intake recommended",
        "The generic extractor did not find visible public job-posting candidates on the provided page.",
    )


def _is_enough(quality: ExtractionQuality) -> bool:
    if quality.useful_title_count < 1 or quality.unique_url_count < 1:
        return False
    return any(candidate.description_length >= MIN_USEFUL_DESCRIPTION_CHARS for candidate in quality.candidates)


def _quality_as_dict(quality: ExtractionQuality) -> dict[str, Any]:
    return {
        "label": quality.label,
        "status_code": quality.status_code,
        "final_url": quality.final_url,
        "visible_text_chars": quality.visible_text_chars,
        "candidate_count": quality.candidate_count,
        "useful_title_count": quality.useful_title_count,
        "generic_title_count": quality.generic_title_count,
        "unique_url_count": quality.unique_url_count,
        "average_description_length": quality.average_description_length,
        "warnings": quality.warnings,
        "candidates": [candidate.__dict__ for candidate in quality.candidates],
    }
