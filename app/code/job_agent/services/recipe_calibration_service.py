from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

from job_agent.browser.playwright_probe import slugify_url
from job_agent.config import ROOT
from job_agent.paths import output_dir
from job_agent.services.extraction_quality import quality_as_dict
from job_agent.services.job_board_check_service import validate_public_url
from job_agent.services.job_board_recipe_service import (
    check_recipe_against_html,
    extract_jobs_with_recipe,
)
from job_agent.services.recipes.discovery import (
    discover_application_entries,
    discover_feed_links,
    discover_interactive_pagination_controls,
    discover_listing_expansion,
    discover_pagination_links,
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
from job_agent.services.recipes.mapping import _selectors, job_board_recipe_from_mapping, load_project_job_board_recipe
from job_agent.services.recipes.models import JobBoardRecipe
from job_agent.services.recipes.soup import is_stable_css_class
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
    r"\b(contract|freelance|permanent|full[- ]time|part[- ]time)\b",
    r"\bsalary\b",
    r"(\bEUR\b|\bDKK\b|£|\$|/day|/hour)",
)

KNOWN_DETAIL_URL_TOKENS = (
    "/freelance_projects/",
    "/freelance-projects/",
    "/contract-jobs/",
    "/consultant-jobs/",
    "/remote-jobs/",
    "/job-role/",
    "/vacancies/",
    "/vacancy/",
    "/careers/",
    "/career/",
    "/positions/",
    "/position/",
    "/opportunities/",
    "/opportunity/",
    "/openings/",
    "/opening/",
    "/projects/",
    "/project/",
    "/roles/",
    "/role/",
    "/jobs/",
    "/job/",
    "/jobb/",
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
class DetailLinkFamily:
    token: str
    unique_url_count: int
    card_selector: str
    card_match_count: int
    sample_urls: list[str] = field(default_factory=list)
    sample_texts: list[str] = field(default_factory=list)


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
    session_state_path: str | Path | None = None,
    source_session_scope: str = "",
) -> RecipeCalibrationResult:
    normalized_url = validate_public_url(url)
    root = Path(root)
    recipe = load_project_job_board_recipe(root, recipe_path) if recipe_path else None
    html, final_url, fetch_warnings, use_rendered = _fetch_calibration_html(
        normalized_url,
        recipe=recipe,
        rendered=rendered,
        timeout_seconds=15,
        session_state_path=session_state_path,
    )
    capture_mode = "rendered_html" if use_rendered else "static_html"
    soup = BeautifulSoup(html, "html.parser")
    visible_text = soup.get_text("\n", strip=True)
    learning_exploration = explore_learning_material(html, final_url)
    candidates = discover_candidate_elements(html, max_candidates=max_candidates, base_url=final_url)
    pagination_observations = discover_pagination_links(html, final_url)
    feed_observations = discover_feed_links(html, final_url)
    ajax_pagination_observations = discover_ajax_pagination_templates(html, final_url)
    interactive_pagination_observations = discover_interactive_pagination_controls(html)
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
        session_state_path=session_state_path,
    )
    artifact_dir = _artifact_dir(root, final_url)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    feed_artifacts, feed_warnings = _capture_feed_artifacts(
        feed_observations.links,
        artifact_dir=artifact_dir,
        timeout_seconds=15,
    )
    api_observations, api_warnings = discover_api_access_candidates(
        html,
        final_url,
        artifact_dir=artifact_dir,
        timeout_seconds=15,
    )
    recipe_blueprint = build_recipe_blueprint(
        html,
        final_url,
        capture_mode=capture_mode,
        detail_html=detail_html,
        detail_url=detail_final_url or detail_sample_url,
    )
    api_blueprint = _api_recipe_blueprint(api_observations[0], final_url) if api_observations else {}
    if api_blueprint and (
        recipe_blueprint.get("status") == "not_recommended" or api_blueprint.get("confidence") == "high"
    ):
        recipe_blueprint = api_blueprint
    learning_exploration["not_findable"] = _learning_not_findable(
        recipe_blueprint=recipe_blueprint,
        pagination_observations=pagination_observations,
        ajax_pagination_observations=ajax_pagination_observations,
        api_observations=api_observations,
        detail_sample_captured=bool(detail_html),
    )
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
        "observed_feed_links": [asdict(link) for link in feed_observations.links],
        "observed_feed_selector": feed_observations.selector,
        "observed_feed_artifacts": feed_artifacts,
        "observed_ajax_pagination_templates": ajax_pagination_observations,
        "observed_api_candidates": api_observations,
        "observed_interactive_pagination_controls": interactive_pagination_observations,
        "observed_application_entries": [asdict(entry) for entry in application_entries],
        "learning_exploration": learning_exploration,
        "source_session_used": bool(session_state_path),
        "source_session_scope": source_session_scope if session_state_path else "",
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
            fetch_warnings + detail_warnings + feed_warnings + api_warnings,
            pagination_observations,
            application_entries,
            detail_final_url or detail_sample_url,
            recipe_blueprint,
            api_observations,
            feed_observations.links,
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
        warnings=fetch_warnings + detail_warnings + feed_warnings + api_warnings + (audit.warnings if audit else []),
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
    feed_blueprint = _feed_recipe_blueprint(
        html,
        base_url,
        capture_mode=capture_mode,
        accept_token=str(card.get("url_token") or "") if card else "",
    )
    if feed_blueprint and len((feed_blueprint.get("feed_listing_evidence") or {}).get("links") or []) >= 2:
        return feed_blueprint
    if not card:
        if feed_blueprint:
            return feed_blueprint
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
    access = _access_recipe(html, base_url)
    if access:
        recipe["access"] = access
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


def _feed_recipe_blueprint(
    html: str,
    base_url: str,
    *,
    capture_mode: str,
    accept_token: str = "",
) -> dict[str, Any]:
    feed = discover_feed_links(html, base_url)
    if not feed.selector or not feed.links:
        return {}
    recipe: dict[str, Any] = {
        "source_name": "Recipe source",
        "start_url": base_url,
        "mode": capture_mode if capture_mode in {"static_html", "rendered_html"} else "static_html",
        "listing": {
            "card_selector": "item",
            "title_selector": "title",
            "link_selector": ["guid", "link"],
            "location_selector": ["region", "state", "country"],
            "workload_selector": "type",
            "posted_date_selector": ["pubDate", "pubdate"],
            "description_selector": "description",
        },
        "pagination": {
            "strategy": "url",
            "page_link_selector": feed.selector,
            "max_pages": min(len(feed.links) + 1, 25),
            "request_delay_seconds": 1.0,
        },
        "reject": _default_reject_rules(),
        "patterns": _default_patterns(),
        "limits": {
            "max_cards": 500,
            "min_title_length": 8,
            "min_description_length": 0,
        },
    }
    if accept_token:
        recipe["accept"] = {"url_contains": [accept_token]}

    validation_errors: list[str] = []
    try:
        job_board_recipe_from_mapping(recipe, label="feed_recipe_blueprint")
    except ValueError as exc:
        validation_errors.append(str(exc))
    return {
        "status": "draft" if not validation_errors else "not_recommended",
        "confidence": "high" if len(feed.links) >= 2 and not validation_errors else "medium",
        "recipe": recipe if not validation_errors else {},
        "feed_listing_evidence": {
            "selector": feed.selector,
            "links": [asdict(link) for link in feed.links],
        },
        "validation_errors": validation_errors,
        "warnings": validation_errors,
    }


def _capture_feed_artifacts(
    links: list[Any],
    *,
    artifact_dir: Path,
    timeout_seconds: int,
    max_feeds: int = 12,
) -> tuple[list[dict[str, str]], list[str]]:
    artifacts: list[dict[str, str]] = []
    warnings: list[str] = []
    for index, link in enumerate(links[:max_feeds], start=1):
        url = str(getattr(link, "url", "") or "").strip()
        if not url:
            continue
        try:
            text, final_url, fetch_warnings = _fetch_static_html(url, timeout_seconds)
        except ValueError as exc:
            warnings.append(f"Public feed probe failed for {url}: {exc}")
            continue
        warnings.extend(fetch_warnings)
        response_artifact = artifact_dir / f"feed-listing-response-{index}.xml"
        response_artifact.write_text(text, encoding="utf-8")
        request_artifact = artifact_dir / f"feed-listing-request-{index}.json"
        request_artifact.write_text(
            json.dumps({"method": "GET", "url": url, "final_url": final_url}, indent=2),
            encoding="utf-8",
        )
        artifacts.append(
            {
                "url": url,
                "final_url": final_url,
                "request_artifact": _artifact_relative_path(request_artifact, artifact_dir),
                "response_artifact": _artifact_relative_path(response_artifact, artifact_dir),
            }
        )
    if len(links) > max_feeds:
        warnings.append(f"Skipped {len(links) - max_feeds} additional public feed link(s) beyond the safe probe limit.")
    return artifacts, warnings


def discover_api_access_candidates(
    html: str,
    base_url: str,
    *,
    artifact_dir: Path,
    timeout_seconds: int = 15,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Find public page-declared API access shapes without guessing hidden endpoints."""

    warnings: list[str] = []
    evidence_text = html + "\n" + "\n".join(_referenced_public_script_texts(html, base_url, timeout_seconds))
    candidates: list[dict[str, Any]] = []
    sthree_candidate, sthree_warnings = _discover_sthree_search_api_candidate(
        evidence_text,
        base_url,
        artifact_dir=artifact_dir,
        timeout_seconds=timeout_seconds,
    )
    warnings.extend(sthree_warnings)
    if sthree_candidate:
        candidates.append(sthree_candidate)
    return candidates, warnings


def _discover_sthree_search_api_candidate(
    evidence_text: str,
    base_url: str,
    *,
    artifact_dir: Path,
    timeout_seconds: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    lowered = evidence_text.lower()
    if "apiurl" not in lowered or "brandcode" not in lowered:
        return None, warnings
    api_base = _first_script_value(evidence_text, "apiUrl")
    brand_code = _first_script_value(evidence_text, "brandCode")
    if not api_base or not brand_code:
        return None, ["Page appeared to expose an API config, but apiUrl or brandCode could not be read."]
    language = _first_script_value(evidence_text, "jobLanguage") or _locale_from_url(base_url)
    endpoint = urljoin(api_base.rstrip("/") + "/", "api/services/v2/app/Search/Search")
    body = _sthree_search_body(base_url, brand_code=brand_code, language=language)
    headers = {
        "Content-Type": "application/json",
        "Abp.Localization.CultureName": language,
    }
    request_payload = {
        "method": "POST",
        "url": endpoint,
        "headers": headers,
        "body": body,
    }
    request_artifact = artifact_dir / "api-listing-request-1.json"
    request_artifact.write_text(json.dumps(request_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        payload, final_url, fetch_warnings = _fetch_json_api(
            method="POST",
            url=endpoint,
            timeout_seconds=timeout_seconds,
            headers=headers,
            body=body,
        )
    except ValueError as exc:
        return None, [f"Page-declared API probe failed for {endpoint}: {exc}"]
    warnings.extend(fetch_warnings)
    response_artifact = artifact_dir / "api-listing-response-1.json"
    response_artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    records = _json_path(payload, "result.results")
    if not isinstance(records, list):
        records = []
    total = _int_value(_json_path(payload, "result.hits"))
    field_mapping = _sthree_field_mapping(records[0] if records and isinstance(records[0], dict) else {}, base_url)
    candidate = {
        "kind": "page_declared_sthree_search_api",
        "method": "POST",
        "url": endpoint,
        "final_url": final_url,
        "headers": headers,
        "body": body,
        "results_path": "result.results",
        "total_path": "result.hits",
        "record_count": len(records),
        "total_count": total,
        "request_artifact": _artifact_relative_path(request_artifact, artifact_dir),
        "response_artifact": _artifact_relative_path(response_artifact, artifact_dir),
        "fields": field_mapping,
        "pagination": {
            "strategy": "offset",
            "offset_param": "resultFrom",
            "offset_start": int(body.get("resultFrom") or 0),
            "page_param": "resultPage",
            "page_start": int(body.get("resultPage") or 0),
            "page_size_param": "resultSize",
            "page_size": int(body.get("resultSize") or 20),
            "max_pages": 5,
            "request_delay_seconds": 1.0,
        },
        "sample_records": [_sample_api_record(record) for record in records[:3] if isinstance(record, dict)],
        "warnings": [],
    }
    if not records:
        candidate["warnings"].append("The page-declared API returned no records for the captured request.")
    if total and len(records) and total > len(records):
        candidate["warnings"].append(
            f"The API reported {total} total record(s); pagination is required for full coverage."
        )
    return candidate, warnings


def _api_recipe_blueprint(candidate: dict[str, Any], base_url: str) -> dict[str, Any]:
    fields = {key: value for key, value in (candidate.get("fields") or {}).items() if value}
    listing_api = {
        "method": candidate.get("method") or "GET",
        "url": candidate.get("url") or "",
        "headers": candidate.get("headers") or {},
        "body": candidate.get("body") or {},
        "results_path": candidate.get("results_path") or "",
        "total_path": candidate.get("total_path") or "",
        "fields": fields,
        "pagination": candidate.get("pagination") or {},
    }
    total = int(candidate.get("total_count") or 0)
    observed = int(candidate.get("record_count") or 0)
    recipe: dict[str, Any] = {
        "source_name": "Recipe source",
        "start_url": base_url,
        "mode": "static_html",
        "listing_api": listing_api,
        "reject": _default_reject_rules(),
        "patterns": _default_patterns(),
        "limits": {
            "max_cards": max(25, min(total or observed or 25, 100)),
            "min_title_length": 8,
            "min_description_length": 0,
        },
    }
    validation_errors: list[str] = []
    try:
        job_board_recipe_from_mapping(recipe, label="api_recipe_blueprint")
    except ValueError as exc:
        validation_errors.append(str(exc))
    return {
        "status": "draft" if not validation_errors else "not_recommended",
        "confidence": "high" if observed >= 3 and not validation_errors else "medium",
        "recipe": recipe if not validation_errors else {},
        "api_listing_evidence": candidate,
        "validation_errors": validation_errors,
        "warnings": validation_errors + list(candidate.get("warnings") or []),
    }


def _referenced_public_script_texts(html: str, base_url: str, timeout_seconds: int) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc.lower()
    texts: list[str] = []
    for script in soup.find_all("script", src=True)[:8]:
        src = str(script.get("src") or "").strip()
        if not src:
            continue
        url = urljoin(base_url, src)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if parsed.netloc.lower() != base_host:
            continue
        try:
            text, _final_url, _warnings = _fetch_static_html(url, timeout_seconds)
        except ValueError:
            continue
        if len(text) <= 500_000:
            texts.append(text)
    return texts


def _first_script_value(text: str, name: str) -> str:
    kebab_name = re.sub(r"(?<!^)([A-Z])", r"-\1", name).lower()
    patterns = [
        rf"\b{re.escape(name)}\s*=\s*['\"]([^'\"]+)['\"]",
        rf"\b{re.escape(name)}\s*:\s*['\"]([^'\"]+)['\"]",
        rf"['\"]{re.escape(name)}['\"]\s*:\s*['\"]([^'\"]+)['\"]",
        rf"data-{kebab_name}\s*=\s*['\"]([^'\"]+)['\"]",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _sthree_search_body(base_url: str, *, brand_code: str, language: str) -> dict[str, Any]:
    query = parse_qs(urlparse(base_url).query)
    body: dict[str, Any] = {
        "resultSize": 20,
        "resultFrom": 0,
        "resultPage": 0,
        "language": language,
        "brandCode": brand_code,
    }
    for target, names in {
        "industry": ["industry"],
        "type": ["type", "jobType"],
        "country": ["country"],
    }.items():
        values = _query_values(query, names)
        body[target] = values
    for target, names in {
        "keywords": ["keywords", "keyword", "searchKeyword", "query"],
        "location": ["location"],
        "searchRadius": ["searchRadius"],
    }.items():
        values = _query_values(query, names)
        if values:
            body[target] = values[0]
    if "country" not in body:
        body["country"] = []
    return body


def _query_values(query: dict[str, list[str]], names: list[str]) -> list[str]:
    values: list[str] = []
    lowered = {key.lower(): value for key, value in query.items()}
    for name in names:
        for value in lowered.get(name.lower(), []):
            text = str(value).strip()
            if text:
                values.append(text)
    return values


def _sthree_field_mapping(record: dict[str, Any], base_url: str) -> dict[str, str]:
    fields: dict[str, str] = {
        "title": _first_existing_key(record, ["title", "jobTitle", "name"]),
        "location": _first_existing_key(record, ["location", "locationName", "city", "country"]),
        "remote": _first_existing_key(record, ["remoteWorkingAvailable", "remote", "isRemote"]),
        "rate": _first_existing_key(record, ["salaryText", "salary", "rate", "payRate"]),
        "workload": _first_existing_key(record, ["jobType", "type", "employmentType"]),
        "posted_date": _first_existing_key(record, ["postDate", "postedDate", "datePosted"]),
        "start_date": _first_existing_key(record, ["startDate"]),
        "description_html": _first_existing_key(record, ["description", "descriptionHtml", "jobDescription"]),
        "job_id": _first_existing_key(record, ["jobReference", "reference", "id"]),
        "raw_text": "",
    }
    url_field = _first_existing_key(record, ["url", "jobUrl", "link"])
    if url_field:
        fields["url"] = url_field
    elif record.get("slug") and record.get("jobReference"):
        fields["url_template"] = _sthree_url_template(base_url)
    return {key: value for key, value in fields.items() if value}


def _first_existing_key(record: dict[str, Any], keys: list[str]) -> str:
    lowered = {key.lower(): key for key in record}
    for key in keys:
        actual = lowered.get(key.lower())
        value = record.get(actual) if actual else None
        if actual and value is not None and value != "":
            return actual
    return ""


def _sthree_url_template(base_url: str) -> str:
    parsed = urlparse(base_url)
    locale = _locale_from_url(base_url)
    prefix = f"/{locale}" if locale else ""
    return f"{parsed.scheme}://{parsed.netloc}{prefix}/job/{{slug}}/{{jobReference}}/"


def _locale_from_url(url: str) -> str:
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    if path_parts and re.fullmatch(r"[a-z]{2}-[a-z]{2}", path_parts[0], re.I):
        return path_parts[0].lower()
    return "en-gb"


def _sample_api_record(record: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "title",
        "jobTitle",
        "name",
        "slug",
        "jobReference",
        "location",
        "salaryText",
        "jobType",
        "postDate",
        "startDate",
    ]
    sample = {key: record.get(key) for key in keys if key in record}
    if "description" in record:
        sample["description_preview"] = _preview(str(record.get("description") or ""), 240)
    return sample


def _json_path(value: Any, path: str) -> Any:
    current = value
    for token in [part for part in str(path or "").strip("$.").split(".") if part]:
        if isinstance(current, dict):
            current = current.get(token)
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _artifact_relative_path(path: Path, artifact_dir: Path) -> str:
    try:
        return path.relative_to(artifact_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _fetch_calibration_html(
    normalized_url: str,
    *,
    recipe: JobBoardRecipe | None,
    rendered: bool | None,
    timeout_seconds: int,
    session_state_path: str | Path | None = None,
) -> tuple[str, str, list[str], bool]:
    if rendered is True or (rendered is None and recipe and recipe.mode == "rendered_html"):
        html, final_url, warnings = _fetch_rendered_calibration_html(
            normalized_url,
            timeout_seconds,
            session_state_path=session_state_path,
        )
        return html, final_url, warnings, True
    if rendered is False or recipe:
        html, final_url, warnings = _fetch_static_calibration_html(
            normalized_url,
            timeout_seconds,
            session_state_path=session_state_path,
        )
        return html, final_url, warnings, False

    html, final_url, warnings = _fetch_static_calibration_html(
        normalized_url,
        timeout_seconds,
        session_state_path=session_state_path,
    )
    if _static_capture_has_listing_evidence(html, final_url):
        return html, final_url, warnings, False
    try:
        rendered_html, rendered_final_url, rendered_warnings = _fetch_rendered_calibration_html(
            normalized_url,
            timeout_seconds,
            session_state_path=session_state_path,
        )
    except (RuntimeError, ValueError) as exc:
        return (
            html,
            final_url,
            warnings
            + _client_rendered_job_search_warnings(html, final_url)
            + [
                "Static capture did not expose stable job-list evidence, and browser-rendered capture was unavailable: "
                f"{exc}"
            ],
            False,
        )
    if _static_capture_has_listing_evidence(rendered_html, rendered_final_url):
        return rendered_html, rendered_final_url, warnings + rendered_warnings, True
    return (
        html,
        final_url,
        warnings
        + rendered_warnings
        + _client_rendered_job_search_warnings(html, final_url)
        + _rendered_blocker_warnings(rendered_html)
        + ["Static and browser-rendered captures both lacked stable job-list evidence."],
        False,
    )


def _client_rendered_job_search_warnings(html: str, base_url: str) -> list[str]:
    lowered = html.lower()
    if not (
        "apiurl" in lowered
        and ("text/x-handlebars-template" in lowered or "id=hitslist" in lowered or 'id="hitslist"' in lowered)
        and ("job-search" in lowered or "jobsearch" in lowered)
    ):
        return []
    warnings = [
        "The page exposes client-side job-search templates but no rendered job rows. "
        "The agent will not call hidden job-search APIs; browser rendering must expose visible job cards."
    ]
    query = parse_qs(urlparse(base_url).query)
    if "country" not in query and "getcountrybyculture" in lowered:
        warnings.append(
            "The page script appears to default a missing country filter from the page locale. "
            "Use an explicit country-specific source URL when learning this site."
        )
    return warnings


def _rendered_blocker_warnings(html: str) -> list[str]:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
    if "cloudflare" in text and ("sorry, you have been blocked" in text or "attention required" in text):
        return [
            "Browser-rendered capture appears blocked by site protection. "
            "The agent will not bypass bot protection or captcha."
        ]
    return []


def _static_capture_has_listing_evidence(html: str, base_url: str) -> bool:
    blueprint = build_recipe_blueprint(html, base_url, capture_mode="static_html", detail_html="", detail_url="")
    if blueprint.get("status") == "not_recommended":
        return False
    recipe = blueprint.get("recipe") if isinstance(blueprint, dict) else {}
    listing = recipe.get("listing") if isinstance(recipe, dict) else {}
    return bool(isinstance(listing, dict) and listing.get("card_selector") and listing.get("title_selector"))


def explore_learning_material(html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    known_links = _job_detail_links(soup, base_url, infer_families=False)
    inferred_families = _inferred_detail_link_families(soup, base_url)
    return {
        "known_detail_link_count": len(known_links),
        "inferred_detail_link_families": [asdict(family) for family in inferred_families[:10]],
        "inferred_detail_link_family_count": len(inferred_families),
        "not_findable": [],
    }


def discover_candidate_elements(
    html: str,
    max_candidates: int = 30,
    *,
    base_url: str = "",
) -> list[CandidateElement]:
    soup = BeautifulSoup(html, "html.parser")
    scored: list[tuple[int, Tag]] = []
    seen: set[int] = set()

    if base_url:
        for link, _absolute_url, _token in _job_detail_links(soup, base_url, infer_families=True):
            ancestor = _candidate_ancestor(link)
            _add_candidate(scored, seen, ancestor, _score_candidate(ancestor) + 8)

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
        if any(term in text.lower() for term in TEXT_TERMS) or any(
            re.search(pattern, text, re.I) for pattern in META_PATTERNS
        ):
            _add_candidate(scored, seen, tag, _score_candidate(tag))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [_candidate_element(tag) for _score, tag in scored[:max_candidates]]


def _learning_not_findable(
    *,
    recipe_blueprint: dict[str, Any],
    pagination_observations: list[Any],
    ajax_pagination_observations: list[dict[str, Any]],
    api_observations: list[dict[str, Any]],
    detail_sample_captured: bool,
) -> list[str]:
    missing: list[str] = []
    if recipe_blueprint.get("status") == "not_recommended":
        missing.append("stable_listing_card_selector")
    recipe = recipe_blueprint.get("recipe") if isinstance(recipe_blueprint, dict) else {}
    if (not isinstance(recipe, dict) or not recipe.get("pagination")) and (
        not pagination_observations and not ajax_pagination_observations
    ):
        missing.append("pagination_method")
    if not api_observations:
        missing.append("page_declared_api")
    if not detail_sample_captured:
        missing.append("detail_sample")
    return missing


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
    session_state_path: str | Path | None = None,
) -> tuple[str, str, str, list[str]]:
    if not enabled:
        return "", "", "", []
    sample_url = _detail_sample_url(html, base_url, recipe)
    if not sample_url:
        return "", "", "", ["No job-detail link was found for detail-sample capture."]
    try:
        detail_html, final_url, warnings = (
            _fetch_rendered_calibration_html(sample_url, timeout_seconds, session_state_path=session_state_path)
            if use_rendered
            else _fetch_static_calibration_html(sample_url, timeout_seconds, session_state_path=session_state_path)
        )
    except ValueError as exc:
        return sample_url, "", "", [f"Detail sample capture failed for {sample_url}: {exc}"]
    return sample_url, detail_html, final_url, warnings


def _fetch_static_calibration_html(
    url: str,
    timeout_seconds: int,
    *,
    session_state_path: str | Path | None = None,
) -> tuple[str, str, list[str]]:
    if session_state_path:
        return _fetch_static_html(url, timeout_seconds, session_state_path=session_state_path)
    return _fetch_static_html(url, timeout_seconds)


def _fetch_rendered_calibration_html(
    url: str,
    timeout_seconds: int,
    *,
    session_state_path: str | Path | None = None,
) -> tuple[str, str, list[str]]:
    if session_state_path:
        return _fetch_rendered_html(url, timeout_seconds, session_state_path=session_state_path)
    return _fetch_rendered_html(url, timeout_seconds)


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
    detail_link_families = _inferred_detail_link_families(soup, base_url)
    extra_tokens = [family.token for family in detail_link_families]
    for link, absolute_url, token in _job_detail_links(soup, base_url, extra_tokens=extra_tokens):
        for ancestor in _candidate_ancestors_for_link(link):
            if _likely_noise(ancestor):
                continue
            selector = _card_selector_for_tag(ancestor, soup, token=token)
            if not selector:
                continue
            cards = soup.select(selector)
            if not cards or len(cards) > 150:
                continue
            matching_cards = [
                card
                for card in cards
                if not _likely_noise(card) and _job_detail_links(card, base_url, extra_tokens=extra_tokens)
            ]
            if not matching_cards:
                continue
            card_detail_urls = [
                absolute_url
                for card in matching_cards
                for _link, absolute_url, link_token in _job_detail_links(card, base_url, extra_tokens=extra_tokens)
                if link_token == token
            ]
            unique_detail_url_count = len(set(card_detail_urls))
            effective_match_count = min(unique_detail_url_count or len(matching_cards), len(matching_cards))
            average_links = sum(len(card.find_all("a", href=True)) for card in cards) / len(cards)
            average_text = sum(len(card.get_text(" ", strip=True)) for card in matching_cards) / len(matching_cards)
            score = effective_match_count * 8 + min(average_text, 250) / 20
            if len(matching_cards) > effective_match_count:
                score -= (len(matching_cards) - effective_match_count) * 6
            if unique_detail_url_count > max(len(matching_cards) * 2, 3):
                score -= min(unique_detail_url_count - len(matching_cards), 50)
            selector_lower = selector.lower()
            if any(term in selector_lower for term in ["job", "project", "card", "item"]):
                score += 18
            if any(term in selector_lower for term in ["card", "item"]):
                score += 12
            if "row" in selector_lower and not any(
                term in selector_lower for term in ["job", "project", "card", "item"]
            ):
                score -= 8
            if "info" in selector_lower and not any(term in selector_lower for term in ["card", "item", "row"]):
                score -= 10
            if ancestor.name in {"article", "tr", "li"}:
                score += 8
            if ancestor.name == "a":
                score -= 20
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
                    "unique_url_count": unique_detail_url_count,
                    "url_token": token,
                    "sample_url": absolute_url,
                    "sample_text": _preview(matching_cards[0].get_text(" ", strip=True), 260),
                    "tag": ancestor.name or "",
                }
    if not scored:
        return None
    best = max(scored.values(), key=lambda item: item["score"])
    best_cards = soup.select(best["selector"])[:5]
    best["link_selector"] = _link_selector_for_card(best_cards[0], best["url_token"])
    best["title_selector"] = _title_selector_for_card(best_cards[0], best["link_selector"])
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
        if current.name in {"a", "article", "li", "tr", "section", "div"}:
            ancestors.append(current)
        if len(ancestors) >= 6:
            break
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None
    return ancestors


def _card_selector_for_tag(tag: Tag, soup: BeautifulSoup, *, token: str = "") -> str:
    if tag.name == "a" and tag.get("href"):
        return f'a[href*="{token}"]' if token else "a[href]"
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
    return is_stable_css_class(value)


def _inferred_detail_link_families(soup: BeautifulSoup, base_url: str) -> list[DetailLinkFamily]:
    groups: dict[str, list[tuple[Tag, str, str]]] = defaultdict(list)
    base_host = urlparse(base_url).netloc.lower()
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        absolute_url = urljoin(base_url, href)
        parsed = urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if base_host and parsed.netloc.lower() != base_host:
            continue
        path = parsed.path.lower()
        if not _url_is_probable_detail_url(path):
            continue
        token = _dynamic_detail_url_token(path)
        if not token:
            continue
        text = link.get_text(" ", strip=True)
        if _link_text_is_noise(text, href):
            continue
        groups[token].append((link, absolute_url, text))

    families: list[DetailLinkFamily] = []
    for token, links in groups.items():
        unique_urls = _unique_preserve_order([url for _link, url, _text in links])
        if len(unique_urls) < 3:
            continue
        selector_counts: dict[str, int] = defaultdict(int)
        for link, _url, _text in links:
            for ancestor in _candidate_ancestors_for_link(link):
                if ancestor.name == "a":
                    continue
                if _likely_noise(ancestor):
                    continue
                selector = _card_selector_for_tag(ancestor, soup)
                if selector:
                    selector_counts[selector] += 1
        if not selector_counts:
            continue
        best_selector, best_count = max(selector_counts.items(), key=lambda item: item[1])
        cards = soup.select(best_selector)
        if best_count < 3 or not cards or len(cards) > 150:
            continue
        matching_cards = [card for card in cards if _card_has_link_token(card, token, base_url)]
        if len(matching_cards) < 3:
            continue
        if not _cards_have_job_like_evidence(matching_cards):
            continue
        families.append(
            DetailLinkFamily(
                token=token,
                unique_url_count=len(unique_urls),
                card_selector=best_selector,
                card_match_count=len(matching_cards),
                sample_urls=unique_urls[:5],
                sample_texts=[
                    _preview(card.get_text(" ", strip=True), 180)
                    for card in matching_cards[:3]
                    if card.get_text(" ", strip=True)
                ],
            )
        )
    return sorted(families, key=lambda family: (family.card_match_count, family.unique_url_count), reverse=True)


def _dynamic_detail_url_token(path: str) -> str:
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if len(segments) < 2:
        return ""
    leaf = segments[-1]
    if not _slug_like_leaf(leaf):
        return ""
    prefix_segments = segments[:-1]
    if any(_non_listing_prefix_segment(segment) for segment in prefix_segments):
        return ""
    return "/" + "/".join(prefix_segments) + "/"


def _slug_like_leaf(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized or normalized in {"apply", "login", "search", "jobs", "job", "careers", "career"}:
        return False
    if "." in normalized:
        return False
    return bool(re.search(r"[a-z]", normalized)) and bool(re.search(r"[-0-9]", normalized))


def _non_listing_prefix_segment(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {
        "about",
        "blog",
        "category",
        "categories",
        "company",
        "companies",
        "contact",
        "legal",
        "locations",
        "location",
        "privacy",
        "resources",
        "tag",
        "tags",
    }


def _card_has_link_token(card: Tag, token: str, base_url: str) -> bool:
    for link in card.find_all("a", href=True):
        path = urlparse(urljoin(base_url, str(link.get("href") or ""))).path.lower()
        if token in path and not _link_text_is_noise(link.get_text(" ", strip=True), str(link.get("href") or "")):
            return True
    return False


def _cards_have_job_like_evidence(cards: list[Tag]) -> bool:
    observed = 0
    for card in cards[:5]:
        text = card.get_text(" ", strip=True)
        lowered = text.lower()
        if any(term in lowered for term in TEXT_TERMS) or any(
            re.search(pattern, text, re.I) for pattern in META_PATTERNS
        ):
            observed += 1
    return observed >= min(2, len(cards[:5]))


def _unique_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _job_detail_links(
    root: Tag | BeautifulSoup,
    base_url: str,
    *,
    extra_tokens: list[str] | tuple[str, ...] | None = None,
    infer_families: bool = False,
) -> list[tuple[Tag, str, str]]:
    result: list[tuple[Tag, str, str]] = []
    seen: set[str] = set()
    tokens = list(extra_tokens or [])
    if infer_families and isinstance(root, BeautifulSoup):
        tokens.extend(family.token for family in _inferred_detail_link_families(root, base_url))
    links = []
    if isinstance(root, Tag) and root.name == "a" and root.get("href"):
        links.append(root)
    links.extend(root.find_all("a", href=True))
    for link in links:
        href = str(link.get("href") or "").strip()
        absolute_url = urljoin(base_url, href)
        if _same_url_without_fragment(absolute_url, base_url):
            continue
        token = _detail_url_token(absolute_url, extra_tokens=tokens)
        if not token or absolute_url in seen:
            continue
        if _link_text_is_noise(link.get_text(" ", strip=True), href):
            continue
        seen.add(absolute_url)
        result.append((link, absolute_url, token))
    return result


def _same_url_without_fragment(left: str, right: str) -> bool:
    return urldefrag(left).url.rstrip("/") == urldefrag(right).url.rstrip("/")


def _detail_url_token(url: str, *, extra_tokens: list[str] | tuple[str, ...] | None = None) -> str:
    path = urlparse(url).path.lower()
    if not _url_is_probable_detail_url(path):
        return ""
    for token in [*(extra_tokens or []), *KNOWN_DETAIL_URL_TOKENS]:
        if token in path:
            return token
    return ""


def _link_text_is_noise(text: str, href: str) -> bool:
    return link_text_is_noise(text, href)


def _url_is_probable_detail_url(path: str) -> bool:
    return is_probable_detail_url(path)


def _link_selector_for_card(card: Tag, token: str) -> str:
    links = [
        link
        for link, _url, link_token in _job_detail_links(card, "https://example.com", extra_tokens=[token])
        if link_token == token
    ]
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


def _title_selector_for_card(card: Tag, link_selector: str) -> str:
    if _selector_has_useful_title(card, link_selector):
        return link_selector
    for heading in card.find_all(["h1", "h2", "h3", "h4"], recursive=True):
        selector = str(heading.name or "")
        if _selector_has_useful_title(card, selector):
            return selector
        link = heading.select_one("a[href]")
        if link and _selector_has_useful_title(card, f"{selector} a"):
            return f"{selector} a"
    for selector in [
        ".job-title a",
        ".job-title",
        ".title a",
        ".title",
        ".position a",
        ".position",
        '[class*="title"] a',
        '[class*="title"]',
    ]:
        if _selector_has_useful_title(card, selector):
            return selector
    return link_selector or "a"


def _selector_has_useful_title(card: Tag, selector: str) -> bool:
    text = _selected_text(card, selector)
    return bool(text and title_quality(text) == "useful")


def _listing_recipe_from_card(card: dict[str, Any]) -> dict[str, Any]:
    card_selector = str(card["selector"])
    title_selector = str(card.get("title_selector") or "a")
    link_selector = str(card.get("link_selector") or title_selector)
    listing: dict[str, Any] = {
        "card_selector": card_selector,
        "title_selector": title_selector,
        "link_selector": link_selector,
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
        "location_selector": [
            ".job-location",
            ".location",
            '[data-testid="city"]',
            '[data-testid*="location"]',
            '[data-qa*="location"]',
            '[aria-label*="Location"]',
            '[class*="location"]',
            '[id*="location"]',
            ".city",
        ],
        "remote_selector": [
            '[data-testid="remoteInPercent"]',
            '[data-testid*="remote"]',
            ".job-arrangement",
            ".remote",
        ],
        "rate_selector": [
            ".salary",
            ".rate",
            ".job-rate",
            '[data-testid*="salary"]',
            '[data-testid*="rate"]',
            '[class*="salary"]',
            '[class*="rate"]',
        ],
        "workload_selector": [
            '[data-testid="type"]',
            '[data-testid*="type"]',
            ".job_type",
            ".job-type",
            ".type",
            '[class*="employment"]',
            '[class*="workload"]',
        ],
        "posted_date_selector": [
            '[data-testid="created"]',
            '[data-testid*="posted"]',
            '[data-testid*="date"]',
            "time",
            ".posted-date",
            ".date",
        ],
        "start_date_selector": ['[data-testid="beginningText"]', '[data-testid*="start"]', ".start_date"],
        "description_selector": [
            ".job-description",
            ".job-summary",
            ".summary",
            ".description",
            ".excerpt",
            ".left",
            '[data-testid*="description"]',
            '[class*="description"]',
            '[class*="summary"]',
        ],
        "company_selector": [
            '[data-testid="company"]',
            '[data-testid*="company"]',
            "div.project-info > div.mg-b-display-m:first-child",
            ".company",
            ".client",
            ".recruiter",
            '[class*="company"]',
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
    page_query_selector = _page_query_link_selector(soup)
    listing_expansion_selector = _listing_expansion_link_selector(soup)
    if soup.select("a.page-numbers"):
        return {
            "strategy": "url",
            "page_link_selector": "a.page-numbers",
            "next_selector": "a.next.page-numbers" if soup.select("a.next.page-numbers") else next_selector,
            "max_pages": _observed_max_page(soup, default=2),
            "request_delay_seconds": 1.0,
        }
    if soup.select('a[href*="pagenr="]'):
        return {
            "strategy": "url",
            "page_link_selector": 'a[href*="pagenr="]',
            "next_selector": next_selector,
            "max_pages": _observed_max_page(soup, default=2),
            "request_delay_seconds": 1.0,
        }
    observed_links = discover_pagination_links(html, base_url)
    if any("pagenr=" in link.url for link in observed_links):
        return {
            "strategy": "url",
            "page_link_selector": 'a[href*="pagenr="]',
            "next_selector": next_selector,
            "max_pages": _observed_max_page_from_links(observed_links, default=2),
            "request_delay_seconds": 1.0,
        }
    if page_query_selector:
        return {
            "strategy": "url",
            "page_link_selector": page_query_selector,
            "next_selector": _page_query_next_selector(soup),
            "max_pages": _observed_max_page(soup, default=2),
            "request_delay_seconds": 1.0,
        }
    listing_expansion = discover_listing_expansion(html, base_url)
    if listing_expansion.selector and listing_expansion.links:
        selector = listing_expansion.selector
        if 'href*="/categories/"' in selector and discover_feed_links(html, base_url).links:
            selector = listing_expansion_selector or selector
        return {
            "strategy": "url",
            "page_link_selector": selector,
            "max_pages": min(len(listing_expansion.links) + 1, 25),
            "request_delay_seconds": 1.0,
        }
    if listing_expansion_selector:
        observed_links = discover_pagination_links(html, base_url)
        expansion_count = sum(1 for link in observed_links if _looks_like_listing_expansion_label(link.label))
        return {
            "strategy": "url",
            "page_link_selector": listing_expansion_selector,
            "next_selector": "",
            "max_pages": max(2, min(expansion_count + 1, 12)),
            "request_delay_seconds": 1.0,
        }
    if next_selector:
        return {
            "strategy": "url",
            "page_link_selector": next_selector,
            "next_selector": next_selector,
            "max_pages": 2,
            "request_delay_seconds": 1.0,
        }
    ajax_templates = discover_ajax_pagination_templates(html, base_url)
    if ajax_templates:
        return {
            "strategy": "ajax",
            "ajax_url_template": ajax_templates[0]["ajax_url_template"],
            "max_pages": max(2, int(ajax_templates[0].get("observed_page") or 2)),
            "request_delay_seconds": 1.0,
        }
    interactive_controls = discover_interactive_pagination_controls(html)
    if interactive_controls:
        return {
            "strategy": "browser_click",
            "click_selector": _interactive_pagination_selector(html),
            "max_pages": 2,
            "request_delay_seconds": 1.0,
        }
    return {}


def _page_query_link_selector(soup: BeautifulSoup) -> str:
    if soup.select('a.page-link[href*="page="]'):
        return 'a.page-link[href*="page="]'
    if soup.select('a[href*="page="]'):
        return 'a[href*="page="]'
    return ""


def _page_query_next_selector(soup: BeautifulSoup) -> str:
    if soup.select('a.page-link[href*="page="][aria-label*="Next"]'):
        return 'a.page-link[href*="page="][aria-label*="Next"]'
    if soup.select('a[href*="page="][aria-label*="Next"]'):
        return 'a[href*="page="][aria-label*="Next"]'
    return ""


def _listing_expansion_link_selector(soup: BeautifulSoup) -> str:
    for link in soup.find_all("a", href=True):
        if _looks_like_listing_expansion_label(link.get_text(" ", strip=True)):
            href = str(link.get("href") or "")
            if href.lower().split("?", 1)[0].endswith(".rss"):
                continue
            if "/categories/" in href:
                return 'a[href*="/categories/"]:not([href$=".rss"])'
            return "a"
    return ""


def _looks_like_listing_expansion_label(value: str) -> bool:
    normalized = " ".join(value.lower().split())
    return bool(re.search(r"\bview all\s+\d+.+\bjobs?\b", normalized))


def discover_ajax_pagination_templates(html: str, base_url: str) -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(["a", "button", "input", "form", "div", "span"]):
        for attr_name, raw_value in element.attrs.items():
            if attr_name == "href":
                continue
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                _collect_ajax_template_candidate(
                    templates,
                    seen,
                    str(value),
                    base_url,
                    evidence=f"{element.name}[{attr_name}]",
                )
        onclick = str(element.get("onclick") or element.get("onClick") or "")
        if onclick:
            _collect_ajax_template_candidate(templates, seen, onclick, base_url, evidence=f"{element.name}[onclick]")
    for script in soup.find_all("script"):
        _collect_ajax_template_candidate(
            templates,
            seen,
            script.get_text(" ", strip=True),
            base_url,
            evidence="script",
        )
    return templates


def _collect_ajax_template_candidate(
    templates: list[dict[str, Any]],
    seen: set[str],
    value: str,
    base_url: str,
    *,
    evidence: str,
) -> None:
    if not value or not any(token in value.lower() for token in ["page", "offset", "load", "fetch", "ajax", "api"]):
        return
    for candidate in _candidate_ajax_urls(value):
        template, observed_page = _ajax_template_from_url(candidate, base_url)
        if not template or template in seen:
            continue
        seen.add(template)
        templates.append(
            {
                "ajax_url_template": template,
                "observed_page": observed_page,
                "evidence": evidence,
            }
        )


def _candidate_ajax_urls(value: str) -> list[str]:
    candidates: list[str] = []
    for pattern in [
        r"""(?P<url>https?://[^'"\s<>]+)""",
        r"""(?P<url>/[^'"\s<>]+[?&][^'"\s<>]+)""",
        r"""['"](?P<url>[^'"]+[?&][^'"]+)['"]""",
    ]:
        for match in re.finditer(pattern, value):
            url = match.group("url").strip()
            if url not in candidates:
                candidates.append(url)
    stripped = value.strip()
    if ("?" in stripped or re.search(r"/(?:page|pagenr)/\d+\b", stripped, re.I)) and len(stripped) < 300:
        candidates.append(stripped)
    return candidates


def _ajax_template_from_url(value: str, base_url: str) -> tuple[str, int]:
    url = urljoin(base_url, value.strip())
    parsed = urlparse(url)
    observed_page = 0
    query = parsed.query
    for key in ["page", "pagenr", "pageNumber", "pageNo", "pageIndex"]:
        replacement = _replace_query_number(query, key, "{page}" if key != "pageIndex" else "{page_index}")
        if replacement != query:
            observed_page = _query_number(query, key)
            return parsed._replace(query=replacement).geturl(), observed_page
    path = re.sub(r"(?i)(/(?:page|pagenr)/)(\d+)(?=/|$)", r"\1{page}", parsed.path)
    if path != parsed.path:
        match = re.search(r"(?i)/(?:page|pagenr)/(\d+)(?=/|$)", parsed.path)
        observed_page = int(match.group(1)) if match else 0
        return parsed._replace(path=path).geturl(), observed_page
    return "", 0


def _replace_query_number(query: str, key: str, replacement: str) -> str:
    return re.sub(
        rf"(?i)(^|&)({re.escape(key)}=)(\d+)(?=&|$)",
        lambda match: f"{match.group(1)}{match.group(2)}{replacement}",
        query,
    )


def _query_number(query: str, key: str) -> int:
    match = re.search(rf"(?i)(^|&){re.escape(key)}=(\d+)(?=&|$)", query)
    return int(match.group(2)) if match else 0


def _interactive_pagination_selector(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(["button", "a", "input", "div", "span"]):
        if element.name == "input" and str(element.get("type") or "").lower() not in {"button", "submit"}:
            continue
        if element.name in {"div", "span"} and str(element.get("role") or "").lower() != "button":
            continue
        label = element.get_text(" ", strip=True) or str(element.get("value") or element.get("aria-label") or "")
        href = str(element.get("href") or "").strip()
        onclick = str(element.get("onclick") or element.get("onClick") or "")
        classes = [str(item).strip() for item in element.get("class", []) if str(item).strip()]
        haystack = " ".join([label, href, onclick, " ".join(classes)]).lower()
        if not any(token in haystack for token in ["next", "page", "pagination", "paginator", "load more", "more"]):
            continue
        element_id = str(element.get("id") or "").strip()
        if element_id:
            return f"#{element_id}"
        if classes:
            return "." + ".".join(classes[:2])
        role = str(element.get("role") or "").strip()
        if role:
            return f'{element.name}[role="{role}"]'
        aria_label = str(element.get("aria-label") or "").strip()
        if aria_label:
            return f'{element.name}[aria-label="{aria_label}"]'
        if element.name == "a":
            return 'a[role="button"], a[href="#"], a[href^="javascript:"]'
        return str(element.name or "button")
    return 'button, a[role="button"]'


def _access_recipe(html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    visible_text = soup.get_text(" ", strip=True).lower()
    gate_phrases = [
        "sign up free to see more results",
        "sign up to see more results",
        "log in to see more",
        "login to see more",
        "sign in to see more",
        "create an account to see more",
        "register to see more",
    ]
    if not any(phrase in visible_text for phrase in gate_phrases):
        return {}
    host = urlparse(base_url if "://" in base_url else f"https://{base_url}").netloc.lower()
    return {
        "requires_session": True,
        "session_scope": host or base_url,
        "setup_hint": "Connect a source session before verifying pagination beyond the public listing page.",
    }


def _observed_max_page(soup: BeautifulSoup, default: int) -> int:
    numbers = []
    for link in soup.find_all("a", href=True):
        label = link.get_text(" ", strip=True)
        if label.isdigit():
            numbers.append(int(label))
        query = parse_qs(urlparse(str(link.get("href") or "")).query)
        for key in ["page", "pagenr"]:
            for value in query.get(key, []):
                if str(value).isdigit():
                    numbers.append(int(value))
    return max(default, min(max(numbers or [default]), 50))


def _observed_max_page_from_links(links: list[Any], default: int) -> int:
    numbers = []
    for link in links:
        label = str(getattr(link, "label", "") or "")
        if label.isdigit():
            numbers.append(int(label))
        query = parse_qs(urlparse(str(getattr(link, "url", "") or "")).query)
        for key in ["page", "pagenr"]:
            for value in query.get(key, []):
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
            r"(?:\bStart\s+date\b\s*:?\s*|\bStart\b\s*:\s*)(?P<start_date>asap|\d{1,2}\s*/\s*\d{4}|"
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
    links = [
        {"text": link.get_text(" ", strip=True), "href": str(link.get("href", ""))}
        for link in tag.find_all("a", href=True)
    ]
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
    classes = [str(item) for item in tag.get("class", []) if _class_is_stable(str(item))]
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
    if _tag_matches_selector(root, selector):
        return root.get_text(" ", strip=True)
    match = root.select_one(selector)
    return match.get_text(" ", strip=True) if match else ""


def _tag_matches_selector(tag: Tag, selector: str) -> bool:
    parent = tag.parent
    if not isinstance(parent, (Tag, BeautifulSoup)):
        return False
    try:
        return tag in parent.select(selector)
    except Exception:
        return False


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
    api_observations: list[dict[str, Any]] | None = None,
    feed_links: list[Any] | None = None,
) -> str:
    blueprint_recipe = (recipe_blueprint or {}).get("recipe") or {}
    lines = [
        "# Recipe Calibration Summary",
        "",
        f"URL: {url}",
        f"Capture mode: {capture_mode}",
        f"Candidate regions: {len(candidates)}",
        f"Pagination-looking links: {len(pagination_links or [])}",
        f"Public feed links: {len(feed_links or [])}",
        f"Application entrypoints: {len(application_entries or [])}",
        f"Page-declared API candidates: {len(api_observations or [])}",
        f"Detail sample: {detail_sample_url or 'none'}",
    ]
    if blueprint_recipe:
        listing = blueprint_recipe.get("listing") or {}
        listing_api = blueprint_recipe.get("listing_api") or {}
        detail = blueprint_recipe.get("detail") or {}
        pagination = blueprint_recipe.get("pagination") or {}
        if listing_api:
            lines.extend(
                [
                    f"Draft API listing: `{listing_api.get('method', 'GET')} {listing_api.get('url', '')}`",
                    f"Draft API records path: `{listing_api.get('results_path', '')}`",
                    f"Draft API pagination: `{(listing_api.get('pagination') or {}).get('strategy', 'none')}`",
                ]
            )
        elif pagination.get("page_link_selector") and listing.get("card_selector") == "item":
            lines.extend(
                [
                    f"Draft feed item selector: `{listing.get('card_selector', '')}`",
                    f"Draft feed pagination selector: `{pagination.get('page_link_selector', '')}`",
                ]
            )
        else:
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
    if api_observations:
        lines.append("")
        lines.append("Page-declared API observations:")
        for observation in api_observations[:5]:
            lines.append(
                "- "
                f"`{observation.get('method', 'GET')} {observation.get('url', '')}` "
                f"records={observation.get('record_count', 0)} total={observation.get('total_count', 0)} "
                f"response={observation.get('response_artifact', '')}"
            )
    if feed_links:
        lines.append("")
        lines.append("Public feed observations:")
        for link in feed_links[:10]:
            lines.append(f"- `{getattr(link, 'url', '')}` label={getattr(link, 'label', '')}")
    lines.append("")
    lines.append("Top candidate selectors:")
    for candidate in candidates[:10]:
        lines.append(
            f"- `{candidate.selector}` ({candidate.kind}) noise={candidate.likely_noise}: {candidate.text_preview}"
        )
    return "\n".join(lines) + "\n"


def _artifact_dir(root: Path, url: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return output_dir(root) / "recipe-calibration" / f"{timestamp}-{slugify_url(url)}"
