from __future__ import annotations

import re
from datetime import date, datetime

from .models import Job, MatchResult


TECH_TERMS = {
    "abap": 14,
    "abap oo": 8,
    "rap": 12,
    "cds": 10,
    "cds views": 10,
    "odata": 10,
    "gateway": 10,
    "sap gateway": 10,
    "s/4hana": 8,
    "s4hana": 8,
    "ecc": 6,
    "debugging": 6,
    "aunit": 5,
    "hana performance": 7,
    "clean core": 6,
    "bapi": 5,
    "badi": 5,
    "user exits": 5,
}

MODULE_TERMS = {"qm": 7, "wm": 7, "ewm": 7, "mm": 7, "sd": 5, "pp": 5, "cats": 5, "mdg": 5}
CONTRACT_TERMS = {"contract": 8, "freelance": 8, "interim": 6, "temporary": 4}
PROJECT_TERMS = ("project manager", "transition manager", "service delivery manager", "coordination", "technical lead")
FRONTEND_PATTERNS = (r"\bpure\s+fiori\b", r"\bui5\s+frontend\b", r"\bfrontend\s+ui5\b")
FUNCTIONAL_PATTERNS = (r"\bpure\s+functional\b", r"functional consultant")
LANGUAGE_NAMES = ("dutch", "french", "portuguese", "german")


def score_job(job: Job, profile: dict, today: date | None = None) -> MatchResult:
    today = today or date.today()
    text = _job_text(job)
    components = {
        "technical_match": _score_terms(text, TECH_TERMS),
        "module_match": _score_terms(text, MODULE_TERMS),
        "contract_fit": _contract_fit(text, job),
        "location_fit": _location_fit(job, profile),
        "seniority_fit": _seniority_fit(text),
        "leadership_project_management_interest": _project_interest(text),
        "language_risk": _language_risk(text, job),
        "frontend_or_functional_risk": _frontend_functional_risk(text),
        "freshness_risk": _freshness_risk(job, today),
        "rate_visibility_or_rate_fit": _rate_fit(job),
    }
    matched_keywords = _matched_terms(text, {**TECH_TERMS, **MODULE_TERMS})
    reasons: list[str] = []
    concerns: list[str] = []
    missing_information: list[str] = []

    _add_positive_reasons(components, matched_keywords, reasons)
    _add_concerns(job, components, text, profile, concerns)
    _add_missing(job, missing_information)

    exclusion_reason = _exclusion_reason(job, today, text)
    raw_score = sum(components.values())
    total_score = max(0, min(100, raw_score))

    if exclusion_reason:
        category = "excluded"
        total_score = 0
    elif _is_fiori_adjacent_partial(text):
        category = "exploratory"
        total_score = min(total_score, 68)
    elif _is_exploratory_pm(text, components):
        category = "exploratory"
        total_score = min(total_score, 68)
    elif total_score >= 70:
        category = "strong"
    elif total_score >= 45:
        category = "exploratory"
    else:
        category = "weak"

    recommended_angle = _recommended_angle(job, category, matched_keywords, text)
    if not reasons:
        reasons.append("Some SAP relevance found, but the role needs manual review.")

    return MatchResult(
        total_score=total_score,
        category=category,
        components=components,
        reasons=_dedupe(reasons),
        concerns=_dedupe(concerns),
        missing_information=_dedupe(missing_information),
        recommended_angle=recommended_angle,
        exclusion_reason=exclusion_reason,
        matched_keywords=_dedupe(matched_keywords),
    )


def _job_text(job: Job) -> str:
    return " ".join(
        str(part)
        for part in [
            job.title,
            job.role_category,
            job.description,
            job.location,
            job.remote,
            job.rate,
            job.contract_duration,
            job.start_date,
            job.deadline,
            " ".join(job.languages),
            " ".join(job.required_languages),
            " ".join(job.required_skills),
            " ".join(job.required_modules),
            job.raw_text,
        ]
        if part
    ).lower()


def _score_terms(text: str, terms: dict[str, int]) -> int:
    score = 0
    for term, points in terms.items():
        if re.search(rf"\b{re.escape(term)}\b", text):
            score += points
    return min(score, 55 if terms is TECH_TERMS else 25)


def _contract_fit(text: str, job: Job) -> int:
    score = _score_terms(text, CONTRACT_TERMS)
    if job.contract_duration and job.contract_duration != "Not listed":
        score += 5
    if job.workload and job.workload != "Not listed":
        score += 2
    return min(score, 20)


def _matched_terms(text: str, terms: dict[str, int]) -> list[str]:
    return [term.upper() for term in terms if re.search(rf"\b{re.escape(term)}\b", text)]


def _location_fit(job: Job, profile: dict) -> int:
    text = f"{job.location} {job.remote}".lower()
    if any(term in text for term in ["remote", "hybrid"]):
        return 7
    preferred = profile.get("location_policy", {}).get("preferred_regions", [])
    if any(str(region).split()[0].lower() in text for region in preferred):
        return 6
    if job.location == "Not listed":
        return 0
    return 2


def _seniority_fit(text: str) -> int:
    if "junior" in text and "project manager" not in text:
        return -5
    if "senior" in text or "lead" in text:
        return 5
    return 2


def _project_interest(text: str) -> int:
    if any(term in text for term in PROJECT_TERMS):
        score = 10
        if "technical background" in text or "sap technical" in text:
            score += 12
        if "sap process" in text or "delivery tracking" in text or "stakeholder coordination" in text:
            score += 8
        return min(score, 28)
    return 0


def _language_risk(text: str, job: Job) -> int:
    if "english required" in text or "english is enough" in text or "english sufficient" in text:
        return 0
    penalty = 0
    for language in LANGUAGE_NAMES:
        if re.search(rf"\bmandatory\s+{language}\b|\b{language}\s+required\b|\bfluent\s+{language}\b", text):
            penalty -= 25
    return max(penalty, -35)


def _frontend_functional_risk(text: str) -> int:
    penalty = 0
    if any(re.search(pattern, text) for pattern in FRONTEND_PATTERNS):
        penalty -= 20
    elif "fiori" in text or "ui5" in text:
        penalty -= 6
    if any(re.search(pattern, text) for pattern in FUNCTIONAL_PATTERNS) and "abap" not in text:
        penalty -= 20
    return penalty


def _freshness_risk(job: Job, today: date) -> int:
    posted = _parse_date(job.posted_date)
    deadline = _parse_date(job.deadline)
    if posted and (today - posted).days > 120:
        return -100
    if deadline and (today - deadline).days > 21:
        return -100
    if not posted and not deadline:
        return -5
    return 0


def _rate_fit(job: Job) -> int:
    if not job.rate or job.rate == "Not listed":
        return -2
    return 5


def _exclusion_reason(job: Job, today: date, text: str) -> str:
    posted = _parse_date(job.posted_date)
    deadline = _parse_date(job.deadline)
    if posted and (today - posted).days > 120:
        return f"Posting is older than 4 months ({job.posted_date})."
    if deadline and (today - deadline).days > 21:
        return f"Application deadline is more than 3 weeks overdue ({job.deadline})."
    if _language_risk(text, job) <= -25 and "english required" not in text:
        return "Mandatory language requirement appears incompatible."
    return ""


def _add_positive_reasons(components: dict[str, int], matched_keywords: list[str], reasons: list[str]) -> None:
    if components["technical_match"] > 0:
        reasons.append("Technical SAP overlap: " + ", ".join(matched_keywords[:8]) + ".")
    if components["module_match"] > 0:
        module_hits = [term for term in matched_keywords if term.lower() in MODULE_TERMS]
        if module_hits:
            reasons.append("Relevant module exposure: " + ", ".join(module_hits) + ".")
    if components["contract_fit"] > 0:
        reasons.append("Contract/freelance format appears aligned with preferences.")
    if components["leadership_project_management_interest"] > 0:
        reasons.append("Includes project coordination, delivery, or leadership angle.")
    if components["location_fit"] > 0:
        reasons.append("Location or remote setup appears potentially workable.")


def _add_concerns(job: Job, components: dict[str, int], text: str, profile: dict, concerns: list[str]) -> None:
    if components["language_risk"] < 0:
        concerns.append("Language requirement may need manual confirmation.")
    if "fiori" in text or "ui5" in text:
        concerns.append(profile.get("skills", {}).get("caveats", {}).get("fiori", "Clarify Fiori/UI5 depth."))
    if any(term in text for term in PROJECT_TERMS):
        concerns.append(profile.get("skills", {}).get("caveats", {}).get("project_management", "Clarify project management ownership depth."))
    if components["freshness_risk"] < 0 and components["freshness_risk"] > -100:
        concerns.append("Freshness is uncertain because no reliable posting date or deadline was found.")
    if components["rate_visibility_or_rate_fit"] < 0:
        concerns.append("Rate or salary is not listed.")
    if job.source_confidence in {"low", "unknown"}:
        concerns.append("Extraction confidence is low; verify source details manually.")


def _add_missing(job: Job, missing: list[str]) -> None:
    for label, value in [
        ("rate", job.rate),
        ("contract duration", job.contract_duration),
        ("start date", job.start_date),
        ("deadline", job.deadline),
        ("workload", job.workload),
        ("posted date", job.posted_date),
    ]:
        if not value or value == "Not listed":
            missing.append(label)


def _recommended_angle(job: Job, category: str, matched_keywords: list[str], text: str) -> str:
    if category == "excluded":
        return "Do not prioritize unless manual review overturns the exclusion."
    if "project manager" in text:
        return "Position as SAP technical consultant with coordination/planning experience, not as a formal PM owner."
    if "fiori" in text or "ui5" in text:
        return "Position around ABAP, Gateway/OData, RAP/CDS, and backend support for Fiori-related applications."
    if matched_keywords:
        return "Lead with " + ", ".join(matched_keywords[:5]) + " and concrete SAP delivery examples."
    return "Review manually and keep the application concise."


def _is_exploratory_pm(text: str, components: dict[str, int]) -> bool:
    return any(term in text for term in PROJECT_TERMS) and components["technical_match"] < 25


def _is_fiori_adjacent_partial(text: str) -> bool:
    if "fiori" not in text and "ui5" not in text:
        return False
    backend_central = "backend integration is central" in text or "gateway" in text or "odata" in text
    has_rap = "rap" in text
    return backend_central and not has_rap


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text or text == "Not listed":
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result
