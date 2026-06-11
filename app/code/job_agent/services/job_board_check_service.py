from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from job_agent.services.extraction_quality import (
    MIN_USEFUL_DESCRIPTION_CHARS,
    ExtractionQuality,
    candidate_quality,
    quality_as_dict,
)
from job_agent.sources import extract_generic_jobs_from_html

PUBLIC_URL_MESSAGE = (
    "Only public http(s) job-board or recruiter pages are supported. "
    "Do not use login, session, captcha, private-network, or protected URLs."
)


@dataclass
class CompatibilityFinding:
    label: str
    status: str
    detail: str


@dataclass
class CompatibilityReport:
    url: str
    normal_html: ExtractionQuality
    rendered_page: ExtractionQuality | None
    recommendation: str
    recommendation_reason: str
    boundaries: list[str] = field(default_factory=list)
    input_type: str = "public URL"
    recipe_preview: Any | None = None
    findings: list[CompatibilityFinding] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "input_type": self.input_type,
            "normal_html": quality_as_dict(self.normal_html),
            "rendered_page": quality_as_dict(self.rendered_page) if self.rendered_page else None,
            "recommendation": self.recommendation,
            "recommendation_reason": self.recommendation_reason,
            "boundaries": self.boundaries,
            "findings": [finding.__dict__ for finding in self.findings],
        }


def check_job_board_compatibility(
    url: str,
    render: bool = True,
    timeout_seconds: int = 15,
    recipe_path: str | Path = "",
    base_url: str = "",
    detail_input_value: str | Path = "",
    root: Path | None = None,
) -> CompatibilityReport:
    value = str(url).strip()
    if _is_public_url(value):
        normalized_url = validate_public_url(value)
        input_type = "public URL"
        normal_html = _extract_from_http(normalized_url, timeout_seconds=timeout_seconds)
        rendered_page = _extract_from_playwright(normalized_url, timeout_seconds=timeout_seconds) if render else None
        boundaries = [
            "Fetched only the provided URL with a polite timeout.",
            "No login, session, cookie, captcha, bot-protection bypass, endpoint discovery, or site scanning.",
            "Playwright rendering, when enabled, navigates only to the same provided page.",
        ]
    else:
        normalized_url = value
        input_type = "local HTML"
        normal_html = _extract_from_local_html(value, base_url=base_url, root=root)
        rendered_page = None
        boundaries = [
            "Used the provided local HTML file; no network request was made for the listing page.",
            "No login, session, cookie, captcha, bot-protection bypass, endpoint discovery, or site scanning.",
            "Playwright rendering is skipped for local compatibility checks.",
        ]
    recommendation, reason = _recommend(normal_html, rendered_page)
    recipe_preview = None
    findings: list[CompatibilityFinding] = []
    if str(recipe_path).strip():
        from job_agent.services.recipe_preview_service import preview_recipe

        recipe_preview = preview_recipe(
            recipe_path,
            value,
            base_url=base_url,
            detail_input_value=detail_input_value,
            root=root,
        )
        findings = _recipe_findings(recipe_preview)
        recommendation, reason = _recommend_recipe(recipe_preview, findings)
    return CompatibilityReport(
        url=normalized_url,
        normal_html=normal_html,
        rendered_page=rendered_page,
        recommendation=recommendation,
        recommendation_reason=reason,
        boundaries=boundaries,
        input_type=input_type,
        recipe_preview=recipe_preview,
        findings=findings,
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


def _is_public_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_from_local_html(value: str, base_url: str, root: Path | None) -> ExtractionQuality:
    quality = ExtractionQuality(label="Generic baseline (local HTML)")
    path = Path(value)
    if not path.is_absolute():
        path = (root or Path.cwd()) / path
    if not path.exists():
        raise ValueError(f"Local HTML file not found: {path}")
    html = path.read_text(encoding="utf-8")
    resolved_base_url = base_url.strip()
    quality.final_url = resolved_base_url
    jobs = extract_generic_jobs_from_html(html, base_url=resolved_base_url, source_name="Compatibility check")
    quality.candidates = [candidate_quality(job) for job in jobs]
    if not jobs:
        quality.warnings.append("No plausible job links were found in the local HTML.")
    return quality


def _extract_from_http(url: str, timeout_seconds: int) -> ExtractionQuality:
    quality = ExtractionQuality(label="Generic baseline (initial HTML)")
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
    quality.candidates = [candidate_quality(job) for job in jobs]
    if not jobs:
        quality.warnings.append("No plausible job links were found in the initial HTML.")
    return quality


def _extract_from_playwright(url: str, timeout_seconds: int) -> ExtractionQuality:
    quality = ExtractionQuality(label="Generic baseline (rendered page)")
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
    quality.candidates = [candidate_quality(job) for job in jobs]
    if not jobs:
        quality.warnings.append("No plausible job links were found after rendering the same page.")
    return quality


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
    best = max(
        [quality for quality in [normal_html, rendered_page] if quality], key=lambda item: item.useful_title_count
    )
    if best.candidate_count:
        return (
            "manual intake recommended",
            "The checker found candidate links, but titles or description context are too thin for reliable generic import.",
        )
    return (
        "manual intake recommended",
        "The generic extractor did not find visible public job-posting candidates on the provided page.",
    )


def _recipe_findings(preview: Any) -> list[CompatibilityFinding]:
    findings = []
    if getattr(preview, "capability_checks", None):
        for check in preview.capability_checks:
            if not (check.expected or check.observed):
                continue
            if (
                check.capability in {"pagination_navigation", "pagination_duplicate_pages"}
                and preview.input_type != "public URL"
                and check.status == "skipped"
            ):
                continue
            findings.append(CompatibilityFinding(check.label, _compatibility_status(check.status), check.detail))
    else:
        findings.extend(
            [
                CompatibilityFinding(
                    "Listing cards",
                    "pass" if preview.extracted_job_count else "fail",
                    f"{preview.extracted_job_count} jobs extracted with {preview.useful_titles} useful titles.",
                ),
                CompatibilityFinding(
                    "Job URLs",
                    "pass" if preview.unique_urls else "fail",
                    f"{preview.unique_urls} unique job detail URL(s) found.",
                ),
            ]
        )

    if getattr(preview, "field_checks", None):
        for check in preview.field_checks:
            if check.source == "detail" and preview.detail_sample_input:
                continue
            if not check.expected and check.status != "observed":
                continue
            findings.append(
                CompatibilityFinding(
                    f"Field: {check.label}",
                    _compatibility_status(check.status),
                    check.detail,
                )
            )

    if preview.detail_sample_input:
        detail_description = next(
            (field for field in preview.detail_field_coverage if field.field == "description"),
            None,
        )
        detail_title = next((field for field in preview.detail_field_coverage if field.field == "title"), None)
        ok = bool(
            preview.detail_sample
            and detail_description
            and detail_description.present_count
            and detail_title
            and detail_title.present_count
        )
        if ok:
            for finding in findings:
                if finding.label == "Detail navigation" and finding.status == "fail":
                    finding.status = "pass"
                    finding.detail = (
                        "Detail sample supplied for this local check proved the recipe can parse a job-specific page."
                    )
        findings.append(
            CompatibilityFinding(
                "Detail sample fields",
                "pass" if ok else "fail",
                "Detail sample produced a title and usable description."
                if ok
                else "Detail sample did not produce both a title and usable description.",
            )
        )
    return findings


def _compatibility_status(status: str) -> str:
    if status == "pass":
        return "pass"
    if status == "fail":
        return "fail"
    if status == "skipped":
        return "warn"
    if status in {"observed", "not_expected"}:
        return "info"
    return status or "warn"


def _recommend_recipe(preview: Any, findings: list[CompatibilityFinding]) -> tuple[str, str]:
    failed = [finding for finding in findings if finding.status == "fail"]
    if failed:
        return (
            "recipe needs calibration",
            f"Recipe compatibility failed: {', '.join(finding.label for finding in failed)}.",
        )
    if any(finding.status == "warn" for finding in findings):
        return (
            "recipe partially compatible",
            "The recipe extracts listing jobs, but one or more capabilities or observed fields still need review.",
        )
    return (
        "selected recipe looks compatible",
        "The recipe identified listing jobs, job detail navigation, pagination, and the supplied detail sample fields.",
    )


def _is_enough(quality: ExtractionQuality) -> bool:
    if quality.useful_title_count < 1 or quality.unique_url_count < 1:
        return False
    return any(candidate.description_length >= MIN_USEFUL_DESCRIPTION_CHARS for candidate in quality.candidates)
