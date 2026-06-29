from __future__ import annotations

import re
from typing import Any

from .models import Job, MatchResult, normalize_text
from .scoring import match_engine_config_from_profile

REMOTE_TERMS = ("fully remote", "remote only", "100% remote", "remote eu", "remote uk")
PART_TIME_TERMS = ("part-time", "part time", "reduced workload", "50%", "60%", "80%")


def build_match_highlights(job: Job, match: MatchResult, profile: dict[str, Any]) -> list[str]:
    if match.category == "excluded":
        return []

    reasons: list[str] = []
    highlighting = profile.get("highlighting", {})
    highlighting = highlighting if isinstance(highlighting, dict) else {}
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

    if _has_interest_overlap(text, profile):
        reasons.append("matches configured role interests")
    if _has_visible_high_rate(job.rate, highlighting):
        reasons.append("visible high compensation")
    if _core_match_count(job, match, profile) >= int(highlighting.get("min_core_matches", 3) or 3):
        reasons.append("strong core keyword overlap")

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


def _has_interest_overlap(text: str, profile: dict[str, Any]) -> bool:
    interests = _terms_from_value(profile.get("role_preferences", {}).get("interests", []))
    return any(_term_matches(text, term) for term in interests)


def _has_visible_high_rate(rate: str, highlighting: dict[str, Any]) -> bool:
    text = normalize_text(rate)
    if not text or "not listed" in text:
        return False
    threshold = int(highlighting.get("high_rate_threshold", 700) or 700)
    amounts = [int(value.replace(",", "").replace(".", "")) for value in re.findall(r"\b\d[\d,.]{2,}\b", text)]
    return any(amount >= threshold for amount in amounts)


def _core_match_count(job: Job, match: MatchResult, profile: dict[str, Any]) -> int:
    core_groups = _core_match_groups(profile)
    haystack = _job_text(job)
    matched = normalize_text(" ".join(match.matched_keywords))
    return sum(1 for group in core_groups if group in matched or _term_matches(haystack, group))


def _core_match_groups(profile: dict[str, Any]) -> list[str]:
    highlighting = profile.get("highlighting", {})
    highlighting = highlighting if isinstance(highlighting, dict) else {}
    configured = _terms_from_value(highlighting.get("core_match_groups", []))
    if configured:
        return configured
    match_engine = match_engine_config_from_profile(profile)
    technical_rules = match_engine.get("keyword_groups") or match_engine.get("technical_keyword_groups", [])
    ranked = sorted(
        technical_rules,
        key=lambda rule: int(rule.get("proficiency", rule.get("score", 0)) or 0),
        reverse=True,
    )
    return _dedupe([normalize_text(rule.get("label", "")) for rule in ranked[:5] if rule.get("label")])


def _term_matches(text: str, term: str) -> bool:
    term = term.strip().lower()
    return bool(term and re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))


def _terms_from_value(value: Any) -> list[str]:
    parts = value if isinstance(value, list) else re.split(r"[\n,]+", str(value or ""))
    return _dedupe([normalize_text(part) for part in parts if str(part).strip()])


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result
