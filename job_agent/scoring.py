from __future__ import annotations

import re

from .models import Job, MatchResult


CORE_TERMS = {
    "abap": 12,
    "rap": 10,
    "cds": 8,
    "odata": 8,
    "gateway": 8,
    "s/4hana": 7,
    "s4hana": 7,
    "debugging": 5,
    "aunit": 5,
}

MODULE_TERMS = {
    "qm": 8,
    "wm": 8,
    "mm": 8,
    "ewm": 5,
    "sd": 4,
    "pp": 4,
    "cats": 4,
    "mdg": 4,
}

POSITIVE_TERMS = {
    "contract": 8,
    "freelance": 8,
    "consultant": 5,
    "technical": 5,
    "hybrid": 4,
    "remote": 4,
    "project manager": 8,
    "technical lead": 8,
    "coordination": 6,
}

NEGATIVE_PATTERNS = {
    r"\bpermanent\b": ("Permanent role may be less relevant than contract/freelance work.", 15),
    r"\bemployee\b": ("Employee role may be less relevant than contract/freelance work.", 10),
    r"\bmandatory\s+(dutch|french|german)\b": ("Possible mandatory language mismatch.", 25),
    r"\bpure\s+fiori\b|\bui5 frontend\b": ("Role may be too frontend/UI5-focused.", 20),
    r"\bpure\s+functional\b": ("Role may be too functional and not technical enough.", 20),
}


def score_job(job: Job, profile: dict) -> MatchResult:
    text = " ".join([job.title, job.description, job.location, job.remote, job.rate]).lower()
    score = 0
    reasons: list[str] = []
    concerns: list[str] = []
    matched_keywords: list[str] = []

    for term, points in CORE_TERMS.items():
        if term in text:
            score += points
            matched_keywords.append(term.upper())

    core_hits = [term for term in CORE_TERMS if term in text]
    if core_hits:
        reasons.append("Strong technical SAP match: " + ", ".join(sorted(set(core_hits))).upper() + ".")

    module_hits = [term.upper() for term in MODULE_TERMS if re.search(rf"\b{re.escape(term)}\b", text)]
    for term in module_hits:
        score += MODULE_TERMS[term.lower()]
        matched_keywords.append(term)
    if module_hits:
        reasons.append("Relevant SAP module exposure: " + ", ".join(sorted(set(module_hits))) + ".")

    for term, points in POSITIVE_TERMS.items():
        if term in text:
            score += points
            if term in {"contract", "freelance"}:
                reasons.append("Contract/freelance format appears aligned with preferences.")
            elif term in {"project manager", "technical lead", "coordination"}:
                reasons.append("Includes project coordination or leadership angle.")

    preferred_regions = " ".join(profile.get("location_policy", {}).get("preferred_regions", [])).lower()
    if any(region.lower().split()[0] in text for region in preferred_regions.split()):
        score += 6
        reasons.append("Location appears compatible with preferred regions or relocation policy.")

    for pattern, (message, penalty) in NEGATIVE_PATTERNS.items():
        if re.search(pattern, text):
            score -= penalty
            concerns.append(message)

    if "fiori" in text or "ui5" in text:
        concerns.append(profile.get("skills", {}).get("caveats", {}).get("fiori", "Clarify Fiori/UI5 depth."))

    if not reasons:
        reasons.append("Some SAP relevance found, but the match needs manual review.")

    return MatchResult(score=max(0, min(score, 100)), reasons=_dedupe(reasons), concerns=_dedupe(concerns), matched_keywords=_dedupe(matched_keywords))


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result
