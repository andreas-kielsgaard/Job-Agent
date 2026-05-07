from __future__ import annotations

import re
from typing import Any

from .models import Job, MatchResult, normalize_text

CORE_KEYWORDS = ("abap", "rap", "cds", "odata", "gateway")
PROJECT_INTEREST_TERMS = ("project manager", "coordinator", "coordination", "technical lead", "delivery lead")
REMOTE_TERMS = ("fully remote", "remote only", "100% remote", "remote eu", "remote uk")
PART_TIME_TERMS = ("part-time", "part time", "reduced workload", "50%", "60%", "80%")


def build_match_highlights(job: Job, match: MatchResult, profile: dict[str, Any]) -> list[str]:
    if match.category == "excluded":
        return []

    reasons: list[str] = []
    threshold = int(profile.get("thresholds", {}).get("highlight_score", 75) or 75)
    text = _job_text(job)

    if match.category == "strong":
        reasons.append("strong match category")
    if match.total_score >= threshold:
        reasons.append(f"score {match.total_score}% meets highlight threshold")
    if any(term in normalize_text(job.remote) for term in REMOTE_TERMS):
        reasons.append("fully remote")
    if any(term in text for term in PART_TIME_TERMS):
        reasons.append("reduced workload or part-time")

    preferred_location = _preferred_location_reason(job, profile)
    if preferred_location:
        reasons.append(preferred_location)

    if _has_project_management_interest(text, profile):
        reasons.append("exploratory project/coordination angle")
    if _has_visible_high_rate(job.rate):
        reasons.append("visible high compensation")
    if _core_keyword_count(job, match) >= 3:
        reasons.append("strong ABAP/RAP/CDS/OData/Gateway keyword overlap")

    return _dedupe(reasons)


def _job_text(job: Job) -> str:
    return normalize_text(
        " ".join(
            [
                job.title,
                job.description,
                job.remote,
                job.location,
                job.rate,
                job.workload,
                " ".join(job.required_skills),
                " ".join(job.required_modules),
            ]
        )
    )


def _preferred_location_reason(job: Job, profile: dict[str, Any]) -> str:
    location = normalize_text(job.location)
    remote = normalize_text(job.remote)
    preferred_regions = profile.get("location_policy", {}).get("preferred_regions", [])
    for region in preferred_regions:
        region_text = normalize_text(region)
        if not region_text:
            continue
        if region_text == "remote" and "remote" in remote:
            return "matches preferred remote setup"
        if region_text in location:
            return f"matches preferred location: {region}"
    return ""


def _has_project_management_interest(text: str, profile: dict[str, Any]) -> bool:
    interests = normalize_text(" ".join(profile.get("role_preferences", {}).get("interests", [])))
    if not any(term in interests for term in ("project", "coordination", "manager", "lead")):
        return False
    return any(term in text for term in PROJECT_INTEREST_TERMS)


def _has_visible_high_rate(rate: str) -> bool:
    text = normalize_text(rate)
    if not text or "not listed" in text:
        return False
    amounts = [int(value.replace(",", "").replace(".", "")) for value in re.findall(r"\b\d[\d,.]{2,}\b", text)]
    return any(amount >= 700 for amount in amounts)


def _core_keyword_count(job: Job, match: MatchResult) -> int:
    haystack = _job_text(job)
    haystack += " " + normalize_text(" ".join(match.matched_keywords))
    return sum(1 for keyword in CORE_KEYWORDS if keyword in haystack)


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result
