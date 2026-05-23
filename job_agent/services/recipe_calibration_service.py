from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

from job_agent.browser.playwright_probe import slugify_url
from job_agent.config import ROOT
from job_agent.services.extraction_quality import quality_as_dict
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
    job_board_recipe_from_mapping,
    load_job_board_recipe,
)
from job_agent.services.source_quality_rules import (
    is_probable_detail_url,
    link_text_is_noise,
    text_has_noise_term,
    title_quality,
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
META_PATTERNS = (
    r"\bjob\s*id\b",
    r"\bref(?:erence)?\b",
    r"\bposted\b",
    r"\b\d{4,6}\b",
    r"\b(remote|hybrid|onsite)\b",
    r"\b(contract|freelance|permanent)\b",
    r"(\bEUR\b|\bDKK\b|£|\$|/day|/hour)",
)

UNSUPPORTED_FIELD_LABELS = {
    "application deadline": "Application deadlines are not posting dates.",
    "deadline": "Deadlines are not posting dates.",
    "closing date": "Closing dates are not posting dates.",
    "end date": "End dates are not start dates.",
    "category": "Categories are not workload or contract type.",
}

FIELD_LABEL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("location_selector", ("location", "city", "place")),
    ("remote_selector", ("remote", "remote percentage", "onsite", "on-site", "hybrid")),
    ("rate_selector", ("rate", "salary", "pay", "day rate", "hourly rate")),
    ("workload_selector", ("workload", "work type", "job type", "employment type", "contract type", "type")),
    ("posted_date_selector", ("posted date", "date posted", "published", "created")),
    ("start_date_selector", ("start date", "beginning", "start")),
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
    detail_sample_url: str = ""
    warnings: list[str] = field(default_factory=list)


def capture_recipe_calibration(
    url: str,
    recipe_path: str | None = None,
    rendered: bool | None = None,
    root: Path = ROOT,
    max_candidates: int = 30,
    capture_detail: bool = True,
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
    detail_sample_url, detail_html, detail_final_url, detail_warnings = _capture_detail_sample(
        html,
        final_url,
        recipe=recipe,
        use_rendered=use_rendered,
        timeout_seconds=15,
        enabled=capture_detail,
    )
    recipe_blueprint = build_recipe_blueprint(
        html,
        final_url,
        capture_mode=capture_mode,
        detail_html=detail_html,
        detail_url=detail_final_url or detail_sample_url,
    )

    artifact_dir = _artifact_dir(root, final_url)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "page.html").write_text(html, encoding="utf-8")
    (artifact_dir / "visible-text.txt").write_text(visible_text, encoding="utf-8")
    (artifact_dir / "candidate-elements.html").write_text(_candidate_html(candidates), encoding="utf-8")
    if detail_html:
        (artifact_dir / "detail-sample.html").write_text(detail_html, encoding="utf-8")
        detail_text = BeautifulSoup(detail_html, "html.parser").get_text("\n", strip=True)
        (artifact_dir / "detail-visible-text.txt").write_text(detail_text, encoding="utf-8")
    selector_report = {
        "url": final_url,
        "capture_mode": capture_mode,
        "recipe_path": recipe_path or "",
        "candidates": [asdict(candidate) for candidate in candidates],
        "observed_pagination_links": [asdict(link) for link in pagination_observations],
        "observed_application_entries": [asdict(entry) for entry in application_entries],
        "detail_sample_url": detail_final_url or detail_sample_url,
        "detail_sample_captured": bool(detail_html),
        "recipe_blueprint": recipe_blueprint,
        "selector_audit": asdict(audit) if audit else None,
        "quality": quality_as_dict(quality) if quality else None,
    }
    selector_report_path = artifact_dir / "selector-report.json"
    selector_report_path.write_text(json.dumps(selector_report, indent=2), encoding="utf-8")
    summary_path = artifact_dir / "summary.md"
    summary_path.write_text(
        _summary_markdown(
            final_url,
            capture_mode,
            candidates,
            audit,
            jobs,
            fetch_warnings + detail_warnings,
            pagination_observations,
            application_entries,
            detail_final_url or detail_sample_url,
            recipe_blueprint,
        ),
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
        detail_sample_url=detail_final_url or detail_sample_url,
        warnings=fetch_warnings + detail_warnings + (audit.warnings if audit else []),
    )


def build_recipe_blueprint(
    html: str,
    base_url: str,
    *,
    capture_mode: str = "static_html",
    detail_html: str = "",
    detail_url: str = "",
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    card = _best_listing_card(soup, base_url)
    if not card:
        return {
            "status": "not_recommended",
            "warnings": ["No stable repeated listing card selector was found."],
            "recipe": {},
        }

    token = card["url_token"]
    recipe: dict[str, Any] = {
        "source_name": "Recipe source",
        "start_url": base_url,
        "mode": capture_mode if capture_mode in {"static_html", "rendered_html"} else "static_html",
        "listing": _listing_recipe_from_card(card),
        "accept": {"url_contains": [token]} if token else {},
        "reject": _default_reject_rules(),
        "patterns": _default_patterns(),
        "limits": {
            "max_cards": max(25, min(int(card["match_count"] or 0), 100)),
            "min_title_length": 8,
            "min_description_length": 0,
        },
    }
    pagination = _pagination_recipe(html, base_url)
    if pagination:
        recipe["pagination"] = pagination
    detail, detail_field_observations = _detail_recipe(detail_html)
    if detail:
        recipe["detail"] = detail
    field_observations: dict[str, Any] = {}
    listing_field_observations = card.get("field_observations")
    if isinstance(listing_field_observations, dict):
        field_observations.update(listing_field_observations)
    if detail_field_observations:
        field_observations["detail_label_values"] = detail_field_observations

    validation_errors: list[str] = []
    try:
        job_board_recipe_from_mapping(recipe, label="recipe_blueprint")
    except ValueError as exc:
        validation_errors.append(str(exc))
    observation_warnings = _field_observation_warnings(field_observations)
    result = {
        "status": "draft",
        "confidence": "high" if not validation_errors and int(card["match_count"] or 0) >= 3 else "medium",
        "recipe": recipe,
        "listing_selector_evidence": card,
        "detail_sample_url": detail_url,
        "detail_sample_captured": bool(detail_html),
        "validation_errors": validation_errors,
        "warnings": validation_errors + observation_warnings,
    }
    if field_observations:
        result["field_observations"] = field_observations
    return result


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
        "remote_selector": recipe.listing.remote_selector,
        "rate_selector": recipe.listing.rate_selector,
        "workload_selector": recipe.listing.workload_selector,
        "posted_date_selector": recipe.listing.posted_date_selector,
        "start_date_selector": recipe.listing.start_date_selector,
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


def _capture_detail_sample(
    html: str,
    base_url: str,
    *,
    recipe: JobBoardRecipe | None,
    use_rendered: bool,
    timeout_seconds: int,
    enabled: bool,
) -> tuple[str, str, str, list[str]]:
    if not enabled:
        return "", "", "", []
    sample_url = _detail_sample_url(html, base_url, recipe)
    if not sample_url:
        return "", "", "", ["No job-detail link was found for detail-sample capture."]
    try:
        detail_html, final_url, warnings = (
            _fetch_rendered_html(sample_url, timeout_seconds)
            if use_rendered
            else _fetch_static_html(sample_url, timeout_seconds)
        )
    except ValueError as exc:
        return sample_url, "", "", [f"Detail sample capture failed for {sample_url}: {exc}"]
    return sample_url, detail_html, final_url, warnings


def _detail_sample_url(html: str, base_url: str, recipe: JobBoardRecipe | None) -> str:
    if recipe:
        jobs = extract_jobs_with_recipe(html, base_url, recipe)
        if jobs and jobs[0].url:
            return jobs[0].url
    soup = BeautifulSoup(html, "html.parser")
    links = _job_detail_links(soup, base_url)
    return links[0][1] if links else ""


def _best_listing_card(soup: BeautifulSoup, base_url: str) -> dict[str, Any] | None:
    scored: dict[str, dict[str, Any]] = {}
    for link, absolute_url, token in _job_detail_links(soup, base_url):
        for ancestor in _candidate_ancestors_for_link(link):
            if _likely_noise(ancestor):
                continue
            selector = _card_selector_for_tag(ancestor, soup)
            if not selector:
                continue
            cards = soup.select(selector)
            if not cards or len(cards) > 150:
                continue
            matching_cards = [card for card in cards if not _likely_noise(card) and _job_detail_links(card, base_url)]
            if not matching_cards:
                continue
            average_links = sum(len(card.find_all("a", href=True)) for card in cards) / len(cards)
            average_text = sum(len(card.get_text(" ", strip=True)) for card in matching_cards) / len(matching_cards)
            score = len(matching_cards) * 8 + min(average_text, 250) / 20
            selector_lower = selector.lower()
            if any(term in selector_lower for term in ["job", "project", "card", "item", "row"]):
                score += 18
            if any(term in selector_lower for term in ["card", "item", "row"]):
                score += 12
            if "info" in selector_lower and not any(term in selector_lower for term in ["card", "item", "row"]):
                score -= 10
            if ancestor.name in {"article", "tr", "li"}:
                score += 8
            if len(cards) == 1:
                score -= 20
            if average_links > 8:
                score -= min(average_links, 30)
            if ancestor.find(["nav", "form", "select"]):
                score -= 30
            existing = scored.get(selector)
            if existing is None or score > existing["score"]:
                scored[selector] = {
                    "selector": selector,
                    "score": round(score, 2),
                    "match_count": len(matching_cards),
                    "card_count": len(cards),
                    "url_token": token,
                    "sample_url": absolute_url,
                    "sample_text": _preview(matching_cards[0].get_text(" ", strip=True), 260),
                    "tag": ancestor.name or "",
                }
    if not scored:
        return None
    best = max(scored.values(), key=lambda item: item["score"])
    best_cards = soup.select(best["selector"])[:5]
    best["title_selector"] = _link_selector_for_card(best_cards[0], best["url_token"])
    if best_cards and best_cards[0].name == "tr":
        field_selectors, field_observations = _table_listing_field_selectors(best_cards[0], best_cards)
        best["field_selectors"] = field_selectors
        if field_observations:
            best["field_observations"] = {"listing_table_columns": field_observations}
    else:
        best["field_selectors"] = _available_listing_field_selectors(best_cards)
    return best


def _candidate_ancestors_for_link(link: Tag) -> list[Tag]:
    ancestors: list[Tag] = []
    current: Tag | None = link
    while current and getattr(current, "name", None) and current.name != "[document]":
        if current.name in {"article", "li", "tr", "section", "div"}:
            ancestors.append(current)
        if len(ancestors) >= 6:
            break
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None
    return ancestors


def _card_selector_for_tag(tag: Tag, soup: BeautifulSoup) -> str:
    if tag.name == "tr":
        tbody_rows = soup.select("tbody tr")
        return "tbody tr" if tbody_rows else "tr"
    classes = [str(item) for item in tag.get("class", []) if _class_is_stable(str(item))]
    if classes:
        return f"{tag.name}." + ".".join(classes[:2])
    if tag.get("id"):
        return f"#{tag['id']}"
    return ""


def _class_is_stable(value: str) -> bool:
    return bool(value) and not re.search(r"^[0-9]|reactrenderer|^css-|^js-|ng-|hydrated", value, re.I)


def _job_detail_links(root: Tag | BeautifulSoup, base_url: str) -> list[tuple[Tag, str, str]]:
    result: list[tuple[Tag, str, str]] = []
    seen: set[str] = set()
    for link in root.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        absolute_url = urljoin(base_url, href)
        token = _detail_url_token(absolute_url)
        if not token or absolute_url in seen:
            continue
        if _link_text_is_noise(link.get_text(" ", strip=True), href):
            continue
        seen.add(absolute_url)
        result.append((link, absolute_url, token))
    return result


def _detail_url_token(url: str) -> str:
    path = urlparse(url).path.lower()
    if not _url_is_probable_detail_url(path):
        return ""
    for token in ["/job/", "/project/", "/freelance_projects/", "/jobs/"]:
        if token in path:
            return token
    return ""


def _link_text_is_noise(text: str, href: str) -> bool:
    return link_text_is_noise(text, href)


def _url_is_probable_detail_url(path: str) -> bool:
    return is_probable_detail_url(path)


def _link_selector_for_card(card: Tag, token: str) -> str:
    links = [link for link, _url, link_token in _job_detail_links(card, "https://example.com") if link_token == token]
    if not links:
        return f'a[href*="{token}"]' if token else "a"
    link = links[0]
    if card.name == "tr":
        cell = link.find_parent("td")
        if cell and cell.parent:
            cells = [child for child in cell.parent.find_all("td", recursive=False)]
            if cell in cells:
                return f'td:nth-of-type({cells.index(cell) + 1}) a[href*="{token}"]'
    parent = link.parent if isinstance(link.parent, Tag) else None
    if parent and parent.name in {"h2", "h3", "h4"}:
        return f"{parent.name} a"
    classes = [str(item) for item in link.get("class", []) if _class_is_stable(str(item))]
    if classes:
        return f'a.{".".join(classes[:2])}[href*="{token}"]'
    return f'a[href*="{token}"]' if token else "a"


def _listing_recipe_from_card(card: dict[str, Any]) -> dict[str, Any]:
    card_selector = str(card["selector"])
    title_selector = str(card.get("title_selector") or "a")
    listing: dict[str, Any] = {
        "card_selector": card_selector,
        "title_selector": title_selector,
        "link_selector": title_selector,
    }
    listing.update(card.get("field_selectors") or {})
    return {key: value for key, value in listing.items() if value}


def _table_listing_field_selectors(first_card: Tag, cards: list[Tag]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    headers = _table_headers_for_row(first_card)
    if not headers:
        return {}, []

    selectors: dict[str, str] = {}
    observations: list[dict[str, Any]] = []
    for index, label in enumerate(headers, start=1):
        normalized = _normalize_label(label)
        if not normalized:
            continue
        selector = f"td:nth-of-type({index})"
        values = [_selected_text(card, selector) for card in cards[:5]]
        sample_value = next((value for value in values if value), "")
        if not sample_value:
            continue
        selector_key = classify_recipe_field_label(label)
        unsupported_reason = label_unsupported_reason(label)
        observations.append(
            {
                "index": index,
                "label": label,
                "selector": selector,
                "mapped_selector": selector_key,
                "sample_value": _preview(sample_value, 160),
                "supported": bool(selector_key),
                "warning": unsupported_reason,
            }
        )
        if selector_key and selector_key not in {"title_selector", "link_selector"}:
            selectors.setdefault(selector_key, selector)
    return selectors, observations


def _table_headers_for_row(row: Tag) -> list[str]:
    cells = row.find_all("td", recursive=False)
    if not cells:
        return []
    table = row.find_parent("table")
    if not table:
        return []
    header_rows = table.select("thead tr")
    if not header_rows:
        header_rows = [candidate for candidate in table.find_all("tr") if candidate.find("th")]
    headers: list[str] = []
    for header_row in header_rows:
        candidate_headers = [cell.get_text(" ", strip=True) for cell in header_row.find_all("th", recursive=False)]
        if len(candidate_headers) >= len(headers):
            headers = candidate_headers
    if not headers:
        return []
    if len(headers) < len(cells):
        headers.extend([""] * (len(cells) - len(headers)))
    return headers[: len(cells)]


def _available_listing_field_selectors(cards: list[Tag]) -> dict[str, str]:
    selector_fields = {
        "location_selector": [".job-location", ".location", '[data-testid="city"]', ".city"],
        "remote_selector": ['[data-testid="remoteInPercent"]', ".job-arrangement", ".remote"],
        "workload_selector": ['[data-testid="type"]', ".job-type", ".type"],
        "posted_date_selector": ['[data-testid="created"]', "time", ".posted-date", ".date"],
        "start_date_selector": ['[data-testid="beginningText"]'],
        "description_selector": [".job-summary", ".summary", ".description"],
        "company_selector": [
            '[data-testid="company"]',
            "div.project-info > div.mg-b-display-m:first-child",
            ".company",
            ".client",
            ".recruiter",
        ],
    }
    result: dict[str, str] = {}
    for field_name, selectors in selector_fields.items():
        for selector in selectors:
            if any(card.select(selector) for card in cards):
                result[field_name] = selector
                break
    return result


def _pagination_recipe(html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    next_selector = 'link[rel="next"]' if soup.select('link[rel="next"][href*="pagenr="]') else ""
    if soup.select("a.page-numbers"):
        return {
            "page_link_selector": "a.page-numbers",
            "next_selector": "a.next.page-numbers" if soup.select("a.next.page-numbers") else next_selector,
            "max_pages": _observed_max_page(soup, default=2),
            "request_delay_seconds": 1.0,
        }
    if soup.select('a[href*="pagenr="]'):
        return {
            "page_link_selector": 'a[href*="pagenr="]',
            "next_selector": next_selector,
            "max_pages": _observed_max_page(soup, default=2),
            "request_delay_seconds": 1.0,
        }
    observed_links = discover_pagination_links(html, base_url)
    if any("pagenr=" in link.url for link in observed_links):
        return {
            "page_link_selector": 'a[href*="pagenr="]',
            "next_selector": next_selector,
            "max_pages": _observed_max_page_from_links(observed_links, default=2),
            "request_delay_seconds": 1.0,
        }
    if next_selector:
        return {
            "page_link_selector": next_selector,
            "next_selector": next_selector,
            "max_pages": 2,
            "request_delay_seconds": 1.0,
        }
    return {}


def _observed_max_page(soup: BeautifulSoup, default: int) -> int:
    numbers = []
    for link in soup.find_all("a", href=True):
        label = link.get_text(" ", strip=True)
        if label.isdigit():
            numbers.append(int(label))
        query_pages = parse_qs(urlparse(str(link.get("href") or "")).query).get("pagenr", [])
        for value in query_pages:
            if str(value).isdigit():
                numbers.append(int(value))
    return max(default, min(max(numbers or [default]), 50))


def _observed_max_page_from_links(links: list[Any], default: int) -> int:
    numbers = []
    for link in links:
        label = str(getattr(link, "label", "") or "")
        if label.isdigit():
            numbers.append(int(label))
        query_pages = parse_qs(urlparse(str(getattr(link, "url", "") or "")).query).get("pagenr", [])
        for value in query_pages:
            if str(value).isdigit():
                numbers.append(int(value))
    return max(default, min(max(numbers or [default]), 50))


def _detail_recipe(detail_html: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not detail_html:
        return {}, []
    soup = BeautifulSoup(detail_html, "html.parser")
    detail: dict[str, Any] = {
        "follow": True,
        "max_detail_pages": 5,
        "request_delay_seconds": 1.0,
    }
    if _has_jobposting_json_ld(soup):
        detail["use_json_ld"] = True
    title_selectors = []
    for selector in [".job-single h1", ".project-show-single-page h1", ".modal h1", "main h1", "h1"]:
        if soup.select(selector):
            title_selectors.append(selector)
    if title_selectors:
        detail["title_selector"] = title_selectors[:3]
    label_selectors, label_observations = _detail_label_value_selectors(soup)
    detail.update(label_selectors)
    for field_name, selectors in {
        "description_selector": [".job_txt_wrapp .des_wrapp", ".project-body", ".job-single .job-description"],
        "location_selector": [".job-single .job-location", ".badge-content-city", ".dato_wrapp"],
        "workload_selector": [".job-single .job-type", ".project-header-info-list"],
        "posted_date_selector": [".posted-date"],
        "start_date_selector": [".start-date"],
    }.items():
        if field_name in detail:
            continue
        for selector in selectors:
            if soup.select(selector):
                detail[field_name] = selector
                break
    return detail, label_observations


def _detail_label_value_selectors(soup: BeautifulSoup) -> tuple[dict[str, str], list[dict[str, Any]]]:
    selectors: dict[str, str] = {}
    observations: list[dict[str, Any]] = []
    for element in soup.find_all(["div", "li", "p", "span", "dt", "dd"]):
        label, value = _split_label_value(element)
        if not label or not value:
            continue
        selector_key = classify_recipe_field_label(label)
        unsupported_reason = label_unsupported_reason(label)
        value_selector = _label_value_selector(element)
        observations.append(
            {
                "label": label,
                "selector": value_selector,
                "mapped_selector": selector_key,
                "sample_value": _preview(value, 160),
                "supported": bool(selector_key),
                "warning": unsupported_reason,
            }
        )
        if selector_key and selector_key not in selectors:
            selectors[selector_key] = value_selector
    return selectors, observations


def _split_label_value(element: Tag) -> tuple[str, str]:
    direct_text = " ".join(str(child) for child in element.contents if isinstance(child, NavigableString))
    direct_text = re.sub(r"\s+", " ", direct_text).strip()
    if ":" not in direct_text:
        return "", ""
    label, remainder = direct_text.split(":", 1)
    label = label.strip()
    if not label or len(label) > 40:
        return "", ""
    value_node = _label_value_node(element)
    value = value_node.get_text(" ", strip=True) if value_node else remainder.strip()
    if not value or len(value) > 400:
        return "", ""
    return label, value


def _label_value_node(element: Tag) -> Tag | None:
    for child in element.find_all(["span", "strong", "b"], recursive=False):
        text = child.get_text(" ", strip=True)
        if text:
            return child
    return None


def _label_value_selector(element: Tag) -> str:
    selector = _scoped_selector(element)
    value_node = _label_value_node(element)
    if value_node:
        child_selector = _selector_part(value_node, include_position=False)
        return f"{selector} > {child_selector}"
    return selector


def _scoped_selector(element: Tag) -> str:
    part = _selector_part(element)
    parent = element.parent
    if not isinstance(parent, Tag) or parent.name == "[document]":
        return part
    parent_part = _selector_part(parent)
    if not parent_part or parent.name in {"html", "body"}:
        return part
    return f"{parent_part} > {part}"


def _selector_part(element: Tag, *, include_position: bool = True) -> str:
    classes = [str(item) for item in element.get("class", []) if _class_is_stable(str(item))]
    base = f"{element.name}.{classes[0]}" if classes else str(element.name or "")
    if not include_position:
        return base
    parent = element.parent
    if not isinstance(parent, Tag):
        return base
    siblings = [child for child in parent.find_all(element.name, recursive=False)]
    if len(siblings) <= 1 or element not in siblings:
        return base
    return f"{base}:nth-of-type({siblings.index(element) + 1})"


def _has_jobposting_json_ld(soup: BeautifulSoup) -> bool:
    for script in soup.select('script[type="application/ld+json"]'):
        text = script.string or script.get_text("", strip=True)
        if "JobPosting" in text or '"description"' in text:
            return True
    return False


def _default_reject_rules() -> dict[str, list[str]]:
    return {
        "title_exact": ["Apply", "Apply now", "More info", "View Job", "Services", "Job Search"],
        "title_contains": ["Upload SAP Job", "Improve my CV"],
        "url_contains": ["/services", "/about", "/contact", "/candidates", "/clients", "/category", "/blog", "#"],
    }


def _default_patterns() -> dict[str, str]:
    return {
        "job_id_regex": r"(?:Job ID|Ref):\s*(?P<job_id>[A-Za-z0-9_/-]+)",
        "remote_regex": r"\b(?P<remote>Remote|Hybrid|Hybrid-remote|Office based|On-site|\d+%\s*remote)\b",
        "work_type_regex": r"\b(?P<work_type>Contract|Freelance|Permanent)\b",
        "start_date_regex": (
            r"(?:Start(?: date)?\s*:?\s*)(?P<start_date>asap|\d{1,2}\s*/\s*\d{4}|"
            r"\d{1,2}[./]\d{1,2}[./]\d{4}|[^*\n\r]+?)(?=\s+Duration\b|\s+\d+\s*%\s*workload\b|\s+End date:|$)"
        ),
        "language_regex": r"(?:Languages?:\s*|Language skills:\s*|Fluent in\s+)(?P<language>[A-Z][A-Za-z]+(?:\s*\([^)]*\))?)",
    }


def _field_observation_warnings(field_observations: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    seen: set[str] = set()
    for group in field_observations.values():
        if not isinstance(group, list):
            continue
        for observation in group:
            if not isinstance(observation, dict):
                continue
            warning = str(observation.get("warning") or "").strip()
            label = str(observation.get("label") or "").strip()
            if warning and label:
                message = f"{label}: {warning}"
                if message not in seen:
                    seen.add(message)
                    warnings.append(message)
    return warnings


def classify_recipe_field_label(label: str) -> str:
    normalized = _normalize_label(label)
    if not normalized or label_unsupported_reason(label):
        return ""
    for selector_key, terms in FIELD_LABEL_RULES:
        if any(_label_matches_term(normalized, term) for term in terms):
            return selector_key
    return ""


def label_unsupported_reason(label: str) -> str:
    normalized = _normalize_label(label)
    for term, reason in UNSUPPORTED_FIELD_LABELS.items():
        if _label_matches_term(normalized, term):
            return reason
    return ""


def _normalize_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def _label_matches_term(normalized_label: str, term: str) -> bool:
    normalized_term = _normalize_label(term)
    if not normalized_label or not normalized_term:
        return False
    return bool(re.search(rf"(^|\s){re.escape(normalized_term)}($|\s)", normalized_label))


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
        score = max(score - 12, 1)
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
    if tag.name in {"header", "footer", "nav"} or tag.find_parent(["header", "footer", "nav"]):
        return True
    classes_and_id = " ".join([str(tag.get("id", "")), *[str(item) for item in tag.get("class", [])]]).lower()
    if any(term in classes_and_id for term in ["footer", "header", "nav", "menu", "slideout", "disclaimer", "cookie"]):
        return True
    text = tag.get_text(" ", strip=True).lower()
    if title_quality(text) == "generic":
        return True
    return text_has_noise_term(text)


def _selected_text(root: Tag, selector: str) -> str:
    match = root.select_one(selector)
    return match.get_text(" ", strip=True) if match else ""


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
    detail_sample_url: str = "",
    recipe_blueprint: dict[str, Any] | None = None,
) -> str:
    blueprint_recipe = (recipe_blueprint or {}).get("recipe") or {}
    lines = [
        "# Recipe Calibration Summary",
        "",
        f"URL: {url}",
        f"Capture mode: {capture_mode}",
        f"Candidate regions: {len(candidates)}",
        f"Pagination-looking links: {len(pagination_links or [])}",
        f"Application entrypoints: {len(application_entries or [])}",
        f"Detail sample: {detail_sample_url or 'none'}",
    ]
    if blueprint_recipe:
        listing = blueprint_recipe.get("listing") or {}
        detail = blueprint_recipe.get("detail") or {}
        pagination = blueprint_recipe.get("pagination") or {}
        lines.extend(
            [
                f"Draft card selector: `{listing.get('card_selector', '')}`",
                f"Draft detail follow: {bool(detail.get('follow'))}",
                f"Draft pagination selector: `{pagination.get('page_link_selector', '')}`",
            ]
        )
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
