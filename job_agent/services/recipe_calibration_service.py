from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from job_agent.browser.playwright_probe import slugify_url
from job_agent.config import ROOT
from job_agent.services.extraction_quality import quality_as_dict, title_quality
from job_agent.services.job_board_check_service import validate_public_url
from job_agent.services.job_board_recipe_service import (
    JobBoardRecipe,
    _fetch_rendered_html,
    _fetch_static_html,
    _selectors,
    check_recipe_against_html,
    discover_application_entries,
    discover_pagination_links,
    extract_jobs_with_recipe,
    load_job_board_recipe,
)

JOB_TERMS = (
    "job",
    "jobs",
    "career",
    "vacancy",
    "consultant",
    "sap",
    "abap",
    "contract",
    "role",
    "project",
)
TEXT_TERMS = (
    "sap",
    "abap",
    "rap",
    "cds",
    "odata",
    "gateway",
    "s/4hana",
    "consultant",
    "contract",
    "freelance",
    "remote",
    "hybrid",
)
NOISE_TERMS = (
    "apply now",
    "services",
    "job search",
    "upload sap job",
    "improve my cv",
    "contract staffing",
    "filter",
)
META_PATTERNS = (
    r"\bjob\s*id\b",
    r"\bref(?:erence)?\b",
    r"\bposted\b",
    r"\b\d{4,6}\b",
    r"\b(remote|hybrid|onsite)\b",
    r"\b(contract|freelance|permanent)\b",
    r"(\bEUR\b|\bDKK\b|£|\$|/day|/hour)",
)


@dataclass
class CandidateElement:
    tag: str
    element_id: str
    classes: list[str]
    selector: str
    dom_path: str
    kind: str
    text_preview: str
    links: list[dict[str, str]]
    contains_sap_terms: bool
    likely_noise: bool


@dataclass
class SelectorAudit:
    card_selector: str = ""
    card_match_count: int = 0
    field_match_counts: dict[str, int] = field(default_factory=dict)
    card_text_previews: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RecipeCalibrationResult:
    url: str
    artifact_dir: Path
    capture_mode: str
    candidate_count: int
    summary_path: Path
    selector_report_path: Path
    recipe_extracted_count: int = 0
    card_selector_match_count: int = 0
    warnings: list[str] = field(default_factory=list)


def capture_recipe_calibration(
    url: str,
    recipe_path: str | None = None,
    rendered: bool | None = None,
    root: Path = ROOT,
    max_candidates: int = 30,
) -> RecipeCalibrationResult:
    normalized_url = validate_public_url(url)
    recipe = load_job_board_recipe(Path(recipe_path)) if recipe_path else None
    use_rendered = recipe.mode == "rendered_html" if rendered is None and recipe else bool(rendered)
    html, final_url, fetch_warnings = (
        _fetch_rendered_html(normalized_url, 15) if use_rendered else _fetch_static_html(normalized_url, 15)
    )
    capture_mode = "rendered_html" if use_rendered else "static_html"
    soup = BeautifulSoup(html, "html.parser")
    visible_text = soup.get_text("\n", strip=True)
    candidates = discover_candidate_elements(html, max_candidates=max_candidates)
    pagination_observations = discover_pagination_links(html, final_url)
    application_entries = discover_application_entries(html, final_url)
    audit = audit_recipe_selectors(html, final_url, recipe) if recipe else None
    quality = check_recipe_against_html(html, final_url, recipe) if recipe else None
    jobs = extract_jobs_with_recipe(html, final_url, recipe) if recipe else []

    artifact_dir = _artifact_dir(root, final_url)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "page.html").write_text(html, encoding="utf-8")
    (artifact_dir / "visible-text.txt").write_text(visible_text, encoding="utf-8")
    (artifact_dir / "candidate-elements.html").write_text(_candidate_html(candidates), encoding="utf-8")
    selector_report = {
        "url": final_url,
        "capture_mode": capture_mode,
        "recipe_path": recipe_path or "",
        "candidates": [asdict(candidate) for candidate in candidates],
        "observed_pagination_links": [asdict(link) for link in pagination_observations],
        "observed_application_entries": [asdict(entry) for entry in application_entries],
        "selector_audit": asdict(audit) if audit else None,
        "quality": quality_as_dict(quality) if quality else None,
    }
    selector_report_path = artifact_dir / "selector-report.json"
    selector_report_path.write_text(json.dumps(selector_report, indent=2), encoding="utf-8")
    summary_path = artifact_dir / "summary.md"
    summary_path.write_text(
        _summary_markdown(final_url, capture_mode, candidates, audit, jobs, fetch_warnings, pagination_observations, application_entries),
        encoding="utf-8",
    )
    return RecipeCalibrationResult(
        url=final_url,
        artifact_dir=artifact_dir,
        capture_mode=capture_mode,
        candidate_count=len(candidates),
        summary_path=summary_path,
        selector_report_path=selector_report_path,
        recipe_extracted_count=len(jobs),
        card_selector_match_count=audit.card_match_count if audit else 0,
        warnings=fetch_warnings + (audit.warnings if audit else []),
    )


def discover_candidate_elements(html: str, max_candidates: int = 30) -> list[CandidateElement]:
    soup = BeautifulSoup(html, "html.parser")
    scored: list[tuple[int, Tag]] = []
    seen: set[int] = set()

    for link in soup.find_all("a", href=True):
        link_text = link.get_text(" ", strip=True)
        href = str(link.get("href", ""))
        haystack = f"{link_text} {href}".lower()
        if not any(term in haystack for term in JOB_TERMS):
            continue
        if len(link_text) > 100:
            _add_candidate(scored, seen, link, _score_candidate(link))
        ancestor = _candidate_ancestor(link)
        _add_candidate(scored, seen, ancestor, _score_candidate(ancestor))

    for tag in soup.find_all(["h2", "h3", "h4", "tr", "li", "article", "section", "div"]):
        text = tag.get_text(" ", strip=True)
        if any(term in text.lower() for term in TEXT_TERMS) or any(re.search(pattern, text, re.I) for pattern in META_PATTERNS):
            _add_candidate(scored, seen, tag, _score_candidate(tag))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [_candidate_element(tag) for _score, tag in scored[:max_candidates]]


def audit_recipe_selectors(html: str, base_url: str, recipe: JobBoardRecipe | None) -> SelectorAudit:
    if recipe is None:
        return SelectorAudit()
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(recipe.listing.card_selector)
    audit = SelectorAudit(card_selector=recipe.listing.card_selector, card_match_count=len(cards))
    if not cards:
        audit.warnings.append("listing.card_selector matched 0 elements.")
        return audit

    fields = {
        "title_selector": recipe.listing.title_selector,
        "link_selector": recipe.listing.link_selector,
        "company_selector": recipe.listing.company_selector,
        "location_selector": recipe.listing.location_selector,
        "rate_selector": recipe.listing.rate_selector,
        "posted_date_selector": recipe.listing.posted_date_selector,
        "description_selector": recipe.listing.description_selector,
    }
    first_cards = cards[:5]
    for field_name, selector_value in fields.items():
        selectors = _selectors(selector_value)
        if not selectors:
            continue
        count = sum(1 for card in first_cards if any(card.select(selector) for selector in selectors))
        audit.field_match_counts[field_name] = count
        if field_name in {"title_selector", "link_selector"} and count == 0:
            audit.warnings.append(f"{field_name} matched 0 elements inside first cards.")
    audit.card_text_previews = [_preview(card.get_text(" ", strip=True), 400) for card in first_cards]
    jobs = extract_jobs_with_recipe(html, base_url, recipe)
    if not jobs:
        audit.warnings.append("Recipe extracted 0 jobs from captured HTML.")
    return audit


def _add_candidate(scored: list[tuple[int, Tag]], seen: set[int], tag: Tag, score: int) -> None:
    identity = id(tag)
    if identity in seen or score <= 0:
        return
    seen.add(identity)
    scored.append((score, tag))


def _candidate_ancestor(tag: Tag) -> Tag:
    for ancestor_name in ["article", "li", "tr", "section", "div"]:
        ancestor = tag.find_parent(ancestor_name)
        if ancestor:
            return ancestor
    return tag


def _score_candidate(tag: Tag) -> int:
    text = tag.get_text(" ", strip=True).lower()
    links = tag.find_all("a", href=True)
    score = 0
    score += min(len(links), 3) * 2
    score += sum(2 for term in TEXT_TERMS if term in text)
    score += sum(1 for pattern in META_PATTERNS if re.search(pattern, text, re.I))
    if tag.name == "tr":
        score += 4
    if tag.name in {"article", "li", "section"}:
        score += 2
    if _likely_noise(tag):
        score = max(score - 3, 1)
    return score


def _candidate_element(tag: Tag) -> CandidateElement:
    links = [{"text": link.get_text(" ", strip=True), "href": str(link.get("href", ""))} for link in tag.find_all("a", href=True)]
    text = tag.get_text(" ", strip=True)
    return CandidateElement(
        tag=tag.name or "",
        element_id=str(tag.get("id", "")),
        classes=[str(item) for item in tag.get("class", [])],
        selector=_selector_suggestion(tag),
        dom_path=_dom_path(tag),
        kind=_candidate_kind(tag),
        text_preview=_preview(text, 500),
        links=links,
        contains_sap_terms=any(term in text.lower() for term in ["sap", "abap", "rap", "cds", "odata", "gateway"]),
        likely_noise=_likely_noise(tag),
    )


def _selector_suggestion(tag: Tag) -> str:
    if tag.get("id"):
        return f"#{tag['id']}"
    classes = [str(item) for item in tag.get("class", [])]
    if classes:
        return f"{tag.name}." + ".".join(classes[:3])
    return tag.name or ""


def _dom_path(tag: Tag) -> str:
    parts = []
    current: Tag | None = tag
    while current and getattr(current, "name", None) and current.name != "[document]":
        parts.append(_selector_suggestion(current))
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None
    return " > ".join(reversed(parts[-6:]))


def _candidate_kind(tag: Tag) -> str:
    if tag.name == "tr":
        return "table_row"
    if tag.name in {"h2", "h3", "h4"}:
        return "heading_block"
    if tag.name == "a" and len(tag.get_text(" ", strip=True)) > 100:
        return "single_link_blob"
    if len(tag.find_all("a", href=True)) == 1 and len(tag.get_text(" ", strip=True)) > 120:
        return "single_link_blob"
    if tag.find(["nav", "form", "select"]) or _likely_noise(tag):
        return "filter_nav_block"
    return "card"


def _likely_noise(tag: Tag) -> bool:
    text = tag.get_text(" ", strip=True).lower()
    if title_quality(text) == "generic":
        return True
    return any(term in text for term in NOISE_TERMS)


def _preview(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _candidate_html(candidates: list[CandidateElement]) -> str:
    sections = ["<!doctype html><html><body><h1>Candidate Elements</h1>"]
    for index, candidate in enumerate(candidates, start=1):
        sections.append(f"<section><h2>{index}. {escape(candidate.kind)} - {escape(candidate.selector)}</h2>")
        sections.append(f"<p>{escape(candidate.text_preview)}</p>")
        sections.append("</section>")
    sections.append("</body></html>")
    return "\n".join(sections)


def _summary_markdown(
    url: str,
    capture_mode: str,
    candidates: list[CandidateElement],
    audit: SelectorAudit | None,
    jobs: list[Any],
    warnings: list[str],
    pagination_links: list[Any] | None = None,
    application_entries: list[Any] | None = None,
) -> str:
    lines = [
        "# Recipe Calibration Summary",
        "",
        f"URL: {url}",
        f"Capture mode: {capture_mode}",
        f"Candidate regions: {len(candidates)}",
        f"Pagination-looking links: {len(pagination_links or [])}",
        f"Application entrypoints: {len(application_entries or [])}",
    ]
    if audit:
        lines.extend(
            [
                f"Recipe extracted jobs: {len(jobs)}",
                f"Card selector: `{audit.card_selector}`",
                f"Card selector matches: {audit.card_match_count}",
            ]
        )
        if audit.card_match_count == 0:
            lines.append("Warning: listing.card_selector matched 0 elements.")
        for warning in audit.warnings:
            lines.append(f"Warning: {warning}")
        if jobs:
            lines.append("")
            lines.append("Sample extracted jobs:")
            for job in jobs[:5]:
                lines.append(f"- {job.title} - {job.url}")
    for warning in warnings:
        lines.append(f"Warning: {warning}")
    lines.append("")
    lines.append("Top candidate selectors:")
    for candidate in candidates[:10]:
        lines.append(f"- `{candidate.selector}` ({candidate.kind}) noise={candidate.likely_noise}: {candidate.text_preview}")
    return "\n".join(lines) + "\n"


def _artifact_dir(root: Path, url: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / "output" / "recipe-calibration" / f"{timestamp}-{slugify_url(url)}"
