from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from .models import Job, MatchResult

PROJECT_TERMS = ("project manager", "transition manager", "service delivery manager", "coordination", "technical lead")
FRONTEND_PATTERNS = (r"\bpure\s+fiori\b", r"\bui5\s+frontend\b", r"\bfrontend\s+ui5\b")
FUNCTIONAL_PATTERNS = (r"\bpure\s+functional\b", r"functional consultant")
LANGUAGE_NAMES = ("dutch", "french", "portuguese", "german")
REMOTE_TERMS = ("remote", "hybrid", "work from home", "wfh")
PERMANENT_TERMS = ("permanent", "permanent employee", "full-time employee", "perm role")
RULE_MODES = {"bonus", "required"}
REMOTE_POLICIES = {"required", "strong_preference", "slight_preference", "neutral"}
PERMANENT_POLICIES = {"exclude", "penalize", "ignore"}

DEFAULT_MATCH_ENGINE_CONFIG: dict[str, Any] = {
    "remote_policy": "slight_preference",
    "permanent_policy": "penalize",
    "permanent_penalty": -25,
    "technical_cap": 55,
    "module_cap": 25,
    "technical_keyword_groups": [
        {"label": "ABAP core", "terms": ["abap", "sap abap", "abap oo"], "score": 22, "mode": "bonus"},
        {"label": "RAP", "terms": ["rap", "restful application programming"], "score": 12, "mode": "bonus"},
        {"label": "CDS", "terms": ["cds", "cds views"], "score": 10, "mode": "bonus"},
        {"label": "OData / Gateway", "terms": ["odata", "gateway", "sap gateway"], "score": 10, "mode": "bonus"},
        {"label": "S/4HANA or ECC", "terms": ["s/4hana", "s4hana", "ecc"], "score": 8, "mode": "bonus"},
        {
            "label": "Debugging / quality",
            "terms": ["debugging", "aunit", "hana performance", "clean core"],
            "score": 8,
            "mode": "bonus",
        },
        {
            "label": "Classic ABAP APIs",
            "terms": ["bapi", "badi", "user exits"],
            "score": 6,
            "mode": "bonus",
        },
    ],
    "module_keyword_groups": [
        {"label": "QM", "terms": ["qm", "quality management"], "score": 7, "mode": "bonus"},
        {"label": "WM", "terms": ["wm", "warehouse management"], "score": 7, "mode": "bonus"},
        {"label": "EWM", "terms": ["ewm", "extended warehouse management"], "score": 7, "mode": "bonus"},
        {"label": "MM", "terms": ["mm", "materials management"], "score": 7, "mode": "bonus"},
        {"label": "SD", "terms": ["sd", "sales and distribution"], "score": 5, "mode": "bonus"},
        {"label": "PP", "terms": ["pp", "production planning"], "score": 5, "mode": "bonus"},
        {"label": "CATS", "terms": ["cats"], "score": 5, "mode": "bonus"},
        {"label": "MDG", "terms": ["mdg"], "score": 5, "mode": "bonus"},
    ],
    "contract_keyword_groups": [
        {"label": "Contract / freelance", "terms": ["contract", "freelance"], "score": 8, "mode": "bonus"},
        {"label": "Interim", "terms": ["interim"], "score": 6, "mode": "bonus"},
        {"label": "Temporary", "terms": ["temporary"], "score": 4, "mode": "bonus"},
    ],
}


def default_match_engine_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_MATCH_ENGINE_CONFIG)


def normalize_match_engine_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config if isinstance(config, dict) else {}
    settings = default_match_engine_config()
    settings["remote_policy"] = _choice(config.get("remote_policy"), REMOTE_POLICIES, settings["remote_policy"])
    settings["permanent_policy"] = _choice(
        config.get("permanent_policy"), PERMANENT_POLICIES, settings["permanent_policy"]
    )
    settings["permanent_penalty"] = _int_value(config.get("permanent_penalty"), settings["permanent_penalty"])
    settings["technical_cap"] = max(0, _int_value(config.get("technical_cap"), settings["technical_cap"]))
    settings["module_cap"] = max(0, _int_value(config.get("module_cap"), settings["module_cap"]))
    for key in ["technical_keyword_groups", "module_keyword_groups", "contract_keyword_groups"]:
        if key in config:
            settings[key] = _normalize_rules(config.get(key), [])
    return settings


def match_engine_config_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return normalize_match_engine_config(profile.get("match_engine", {}))


def score_job(job: Job, profile: dict, today: date | None = None) -> MatchResult:
    today = today or date.today()
    settings = match_engine_config_from_profile(profile)
    text = _job_text(job)
    technical_match, technical_matches, missing_technical = _score_rule_groups(
        text, settings["technical_keyword_groups"], settings["technical_cap"]
    )
    module_match, module_matches, missing_modules = _score_rule_groups(
        text, settings["module_keyword_groups"], settings["module_cap"]
    )
    missing_required_rules = missing_technical + missing_modules
    components = {
        "technical_match": technical_match,
        "module_match": module_match,
        "contract_fit": _contract_fit(text, job, settings),
        "location_fit": _location_fit(job, profile, settings),
        "seniority_fit": _seniority_fit(text),
        "leadership_project_management_interest": _project_interest(text),
        "language_risk": _language_risk(text, job),
        "frontend_or_functional_risk": _frontend_functional_risk(text),
        "freshness_risk": _freshness_risk(job, today),
        "rate_visibility_or_rate_fit": _rate_fit(job),
    }
    matched_keywords = _dedupe(technical_matches + module_matches)
    reasons: list[str] = []
    concerns: list[str] = []
    missing_information: list[str] = []

    _add_positive_reasons(components, technical_matches, module_matches, reasons)
    _add_concerns(job, components, text, profile, concerns, missing_required_rules, settings)
    _add_missing(job, missing_information)

    exclusion_reason = _exclusion_reason(job, today, text, settings, missing_required_rules)
    raw_score = sum(components.values())
    total_score = max(0, min(100, raw_score))

    if exclusion_reason:
        category = "excluded"
        total_score = 0
    elif _is_fiori_adjacent_partial(text) or _is_exploratory_pm(text, components):
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


def _score_rule_groups(text: str, rules: list[dict[str, Any]], cap: int) -> tuple[int, list[str], list[str]]:
    score = 0
    matched: list[str] = []
    missing_required: list[str] = []
    for rule in rules:
        label = str(rule.get("label", "")).strip()
        terms = [str(term).strip().lower() for term in rule.get("terms", []) if str(term).strip()]
        if not label or not terms:
            continue
        if any(_term_matches(text, term) for term in terms):
            score += int(rule.get("score", 0))
            matched.append(label)
        elif rule.get("mode") == "required":
            missing_required.append(label)
    return min(score, cap), matched, missing_required


def _contract_fit(text: str, job: Job, settings: dict[str, Any]) -> int:
    if _is_permanent_role(text):
        if settings["permanent_policy"] == "penalize":
            return min(0, int(settings.get("permanent_penalty", -25)))
        return 0
    score, _matches, _missing = _score_rule_groups(text, settings["contract_keyword_groups"], 20)
    if _has_listed_value(job.contract_duration):
        score += 5
    if _has_listed_value(job.workload):
        score += 2
    return min(score, 20)


def _location_fit(job: Job, profile: dict, settings: dict[str, Any]) -> int:
    policy = settings["remote_policy"]
    if policy == "neutral":
        return 0
    text = f"{job.location} {job.remote}".lower()
    if _is_remote_or_hybrid(job):
        if policy in {"required", "strong_preference"}:
            return 12
        return 7
    preferred = profile.get("location_policy", {}).get("preferred_regions", [])
    if policy == "strong_preference":
        return -12
    if policy == "required":
        return 0
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


def _exclusion_reason(
    job: Job,
    today: date,
    text: str,
    settings: dict[str, Any],
    missing_required_rules: list[str],
) -> str:
    posted = _parse_date(job.posted_date)
    deadline = _parse_date(job.deadline)
    if posted and (today - posted).days > 120:
        return f"Posting is older than 4 months ({job.posted_date})."
    if deadline and (today - deadline).days > 21:
        return f"Application deadline is more than 3 weeks overdue ({job.deadline})."
    if _language_risk(text, job) <= -25 and "english required" not in text:
        return "Mandatory language requirement appears incompatible."
    if settings["remote_policy"] == "required" and not _is_remote_or_hybrid(job):
        return "Remote or hybrid setup is required by match settings."
    if settings["permanent_policy"] == "exclude" and _is_permanent_role(text):
        return "Permanent employment is excluded by match settings."
    if missing_required_rules:
        return "Required match rule missing: " + ", ".join(missing_required_rules[:5]) + "."
    return ""


def _add_positive_reasons(
    components: dict[str, int],
    technical_matches: list[str],
    module_matches: list[str],
    reasons: list[str],
) -> None:
    if components["technical_match"] > 0:
        reasons.append("Technical SAP overlap: " + ", ".join(technical_matches[:8]) + ".")
    if components["module_match"] > 0:
        reasons.append("Relevant module exposure: " + ", ".join(module_matches[:8]) + ".")
    if components["contract_fit"] > 0:
        reasons.append("Contract/freelance format appears aligned with preferences.")
    if components["leadership_project_management_interest"] > 0:
        reasons.append("Includes project coordination, delivery, or leadership angle.")
    if components["location_fit"] > 0:
        reasons.append("Location or remote setup appears potentially workable.")


def _add_concerns(
    job: Job,
    components: dict[str, int],
    text: str,
    profile: dict,
    concerns: list[str],
    missing_required_rules: list[str],
    settings: dict[str, Any],
) -> None:
    if components["language_risk"] < 0:
        concerns.append("Language requirement may need manual confirmation.")
    if "fiori" in text or "ui5" in text:
        concerns.append(profile.get("skills", {}).get("caveats", {}).get("fiori", "Clarify Fiori/UI5 depth."))
    if any(term in text for term in PROJECT_TERMS):
        concerns.append(
            profile.get("skills", {})
            .get("caveats", {})
            .get("project_management", "Clarify project management ownership depth.")
        )
    if components["freshness_risk"] < 0 and components["freshness_risk"] > -100:
        concerns.append("Freshness is uncertain because no reliable posting date or deadline was found.")
    if components["rate_visibility_or_rate_fit"] < 0:
        concerns.append("Rate or salary is not listed.")
    if components["contract_fit"] < 0 and _is_permanent_role(text):
        concerns.append("Permanent employment conflicts with the match settings.")
    if settings["remote_policy"] in {"required", "strong_preference"} and not _is_remote_or_hybrid(job):
        concerns.append("Remote or hybrid setup is not visible in the posting.")
    if missing_required_rules:
        concerns.append("Missing required match rule: " + ", ".join(missing_required_rules[:5]) + ".")
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


def _normalize_rules(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else fallback
    rules: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        terms = _terms_from_value(item.get("terms", []))
        score = _int_value(item.get("score"), 0)
        mode = _choice(item.get("mode"), RULE_MODES, "bonus")
        if label and terms and score > 0:
            rules.append({"label": label, "terms": terms, "score": score, "mode": mode})
    return rules


def _terms_from_value(value: Any) -> list[str]:
    parts = value if isinstance(value, list) else re.split(r"[\n,]+", str(value or ""))
    return _dedupe([str(part).strip().lower() for part in parts if str(part).strip()])


def _choice(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _term_matches(text: str, term: str) -> bool:
    term = term.strip().lower()
    return bool(term and re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))


def _is_remote_or_hybrid(job: Job) -> bool:
    text = f"{job.location} {job.remote}".lower()
    return any(term in text for term in REMOTE_TERMS)


def _is_permanent_role(text: str) -> bool:
    return any(_term_matches(text, term) for term in PERMANENT_TERMS)


def _has_listed_value(value: str) -> bool:
    return bool(value and value != "Not listed")


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result
