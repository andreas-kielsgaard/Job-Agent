from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceUrlAssessment:
    status: str
    can_auto_setup: bool
    message: str = ""


JOB_LISTING_PATH_TERMS = {
    "job",
    "jobs",
    "careers",
    "career",
    "vacancies",
    "vacancy",
    "opportunities",
    "roles",
    "job-search",
    "search",
    "remote-jobs",
    "contract",
    "contracts",
    "freelance",
    "consultants",
}
JOB_QUERY_TERMS = {
    "q",
    "query",
    "keyword",
    "keywords",
    "search",
    "term",
    "industry",
    "type",
    "contract",
    "location",
    "remote",
}
LOCALE_SEGMENT = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.IGNORECASE)


def assess_source_setup_url(url: str) -> SourceUrlAssessment:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return SourceUrlAssessment(
            status="invalid",
            can_auto_setup=False,
            message="Save a public http(s) source URL before using automatic source setup.",
        )

    meaningful_segments = [
        segment.lower()
        for segment in parsed.path.split("/")
        if segment.strip() and not LOCALE_SEGMENT.fullmatch(segment.strip())
    ]
    query_keys = {part.split("=", 1)[0].lower() for part in parsed.query.split("&") if part}
    if not meaningful_segments and not query_keys:
        return SourceUrlAssessment(
            status="needs_listing_url",
            can_auto_setup=False,
            message=(
                "This looks like a site homepage. Open it and save the public jobs, careers, or search-results "
                "page before automatic setup starts."
            ),
        )

    path_text = " ".join(meaningful_segments).replace("-", " ").replace("_", " ")
    has_listing_path = any(term.replace("-", " ") in path_text for term in JOB_LISTING_PATH_TERMS)
    has_search_query = bool(query_keys & JOB_QUERY_TERMS)
    if has_listing_path or has_search_query:
        return SourceUrlAssessment(status="likely_listing", can_auto_setup=True)

    return SourceUrlAssessment(
        status="uncertain",
        can_auto_setup=True,
        message=(
            "This URL is not obviously a job listing page. Automatic setup can try it, but a jobs/search page "
            "usually works better."
        ),
    )
