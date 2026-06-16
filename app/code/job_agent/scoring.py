from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from .models import Job, MatchResult

LANGUAGE_NAMES = ("dutch", "french", "portuguese", "german")
REMOTE_TERMS = ("remote", "hybrid", "work from home", "wfh")
PERMANENT_TERMS = ("permanent", "permanent employee", "full-time employee", "perm role")
RULE_MODES = {"bonus", "required"}
REMOTE_POLICIES = {"required", "strong_preference", "slight_preference", "neutral"}
PERMANENT_POLICIES = {"exclude", "penalize", "ignore"}

DEFAULT_MATCH_ENGINE_CONFIG: dict[str, Any] = {
    "remote_policy": "neutral",
    "permanent_policy": "ignore",
    "permanent_penalty": -25,
    "technical_cap": 55,
    "module_cap": 25,
    "technical_keyword_groups": [],
    "module_keyword_groups": [],
    "contract_keyword_groups": [],
}

DEFAULT_AI_REVIEW_POLICY: dict[str, Any] = {
    "min_score": 35,
    "evaluate_categories": ["strong", "exploratory"],
    "trigger_on_highlights": True,
    "trigger_on_review_triggers": True,
    "trigger_on_low_source_confidence": True,
    "evaluate_excluded_with_triggers": False,
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


def normalize_match_review_config(profile: dict[str, Any]) -> dict[str, Any]:
    configured = profile.get("match_review")
    if isinstance(configured, dict):
        rules = _normalize_caveat_rules(configured.get("caveat_rules", []))
    else:
        rules = _legacy_caveat_rules(profile)
    return {"caveat_rules": rules}


def normalize_ai_review_policy(profile: dict[str, Any]) -> dict[str, Any]:
    configured = profile.get("ai_review_policy")
    configured = configured if isinstance(configured, dict) else {}
    policy = deepcopy(DEFAULT_AI_REVIEW_POLICY)
    policy["min_score"] = max(0, _int_value(configured.get("min_score"), policy["min_score"]))
    policy["evaluate_categories"] = _terms_from_value(
        configured.get("evaluate_categories", policy["evaluate_categories"])
    )
    for key in [
        "trigger_on_highlights",
        "trigger_on_review_triggers",
        "trigger_on_low_source_confidence",
        "evaluate_excluded_with_triggers",
    ]:
        policy[key] = _bool_value(configured.get(key), policy[key])
    return policy


def normalize_language_policy(profile: dict[str, Any]) -> dict[str, Any]:
    if "language_policy" not in profile:
        return {
            "mode": "legacy",
            "acceptable": [],
            "fluent": [],
            "exclude_if_mandatory_unmatched": True,
            "penalty": -25,
        }
    configured = profile.get("language_policy")
    configured = configured if isinstance(configured, dict) else {}
    return {
        "mode": "configured",
        "acceptable": _terms_from_value(configured.get("acceptable", [])),
        "fluent": _terms_from_value(configured.get("fluent", [])),
        "exclude_if_mandatory_unmatched": _bool_value(configured.get("exclude_if_mandatory_unmatched"), False),
        "penalty": min(0, _int_value(configured.get("penalty"), -25)),
    }


def score_job(job: Job, profile: dict, today: date | None = None) -> MatchResult:
    today = today or date.today()
    settings = match_engine_config_from_profile(profile)
    review_settings = normalize_match_review_config(profile)
    language_policy = normalize_language_policy(profile)
    text = _job_text(job)
    technical_match, technical_matches, missing_technical = _score_rule_groups(
        text, settings["technical_keyword_groups"], settings["technical_cap"]
    )
    module_match, module_matches, missing_modules = _score_rule_groups(
        text, settings["module_keyword_groups"], settings["module_cap"]
    )
    missing_required_rules = missing_technical + missing_modules
    language_risk = _language_risk(text, job, language_policy)
    components = {
        "technical_match": technical_match,
        "module_match": module_match,
        "contract_fit": _contract_fit(text, job, settings),
        "location_fit": _location_fit(job, profile, settings),
        "seniority_fit": _seniority_fit(text),
        "role_interest_fit": _role_interest_fit(text, profile),
        "language_risk": language_risk,
        "freshness_risk": _freshness_risk(job, today),
        "rate_visibility_or_rate_fit": _rate_fit(job),
    }
    matched_keywords = _dedupe(technical_matches + module_matches)
    review_matches = _matching_review_rules(text, review_settings["caveat_rules"])
    review_triggers = [rule["id"] for rule in review_matches if rule["ai_review"]]
    review_trigger_labels = [rule["label"] for rule in review_matches if rule["ai_review"]]
    reasons: list[str] = []
    concerns: list[str] = []
    missing_information: list[str] = []

    _add_positive_reasons(components, technical_matches, module_matches, reasons)
    concerns.extend(_review_concerns(profile, review_matches))
    _add_concerns(job, components, text, concerns, missing_required_rules, settings)
    _add_missing(job, missing_information)

    exclusion_reason = _exclusion_reason(
        job, today, text, settings, missing_required_rules, language_risk, language_policy
    )
    raw_score = sum(components.values())
    total_score = max(0, min(100, raw_score))

    if exclusion_reason:
        category = "excluded"
        total_score = 0
    elif total_score >= 70:
        category = "strong"
    elif total_score >= 45:
        category = "exploratory"
    else:
        category = "weak"

    recommended_angle = _recommended_angle(category, matched_keywords, review_trigger_labels)
    if not reasons:
        reasons.append("Configured match signals were limited; review manually.")

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
        review_triggers=_dedupe(review_triggers),
        review_trigger_labels=_dedupe(review_trigger_labels),
        deterministic_confidence=_deterministic_confidence(
            category, total_score, concerns, review_triggers, job.source_confidence
        ),
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
    if policy == "required":
        return 0
    if any(str(region).split()[0].lower() in text for region in preferred):
        return 6
    if policy == "strong_preference":
        return -12 if _is_explicitly_onsite(job) else 0
    if not _has_listed_value(job.location) and not _has_listed_value(job.remote):
        return 0
    return 2


def _seniority_fit(text: str) -> int:
    if "junior" in text:
        return -5
    if "senior" in text or "lead" in text:
        return 5
    return 2


def _role_interest_fit(text: str, profile: dict[str, Any]) -> int:
    terms = _terms_from_value(profile.get("role_preferences", {}).get("interests", []))
    target_roles = profile.get("target_roles", {})
    if isinstance(target_roles, dict):
        for key in ["high_match", "exploratory_match"]:
            terms.extend(_terms_from_value(target_roles.get(key, [])))
    target_role_aliases = profile.get("target_role_aliases", {})
    if isinstance(target_role_aliases, dict):
        for values in target_role_aliases.values():
            terms.extend(_terms_from_value(values))
    return 8 if any(_term_matches(text, term) for term in _dedupe(terms)) else 0


def _language_risk(text: str, job: Job, policy: dict[str, Any]) -> int:
    if policy["mode"] == "legacy":
        if "english required" in text or "english is enough" in text or "english sufficient" in text:
            return 0
        penalty = 0
        for language in LANGUAGE_NAMES:
            if _has_mandatory_language(text, language):
                penalty -= 25
        return max(penalty, -35)

    allowed = _dedupe(policy["acceptable"] + policy["fluent"])
    if not allowed:
        return 0
    required = _required_language_terms(text, job, allowed)
    if not required:
        return 0
    unmatched = [language for language in required if not _language_allowed(language, allowed)]
    return int(policy["penalty"]) if unmatched else 0


def _freshness_risk(job: Job, today: date) -> int:
    posted = _parse_date(job.posted_date)
    deadline = _parse_date(job.deadline)
    if posted and (today - posted).days > 120:
        return -100
    if deadline and (today - deadline).days > 21:
        return -100
    return 0


def _rate_fit(job: Job) -> int:
    if not job.rate or job.rate == "Not listed":
        return 0
    return 5


def _exclusion_reason(
    job: Job,
    today: date,
    text: str,
    settings: dict[str, Any],
    missing_required_rules: list[str],
    language_risk: int,
    language_policy: dict[str, Any],
) -> str:
    posted = _parse_date(job.posted_date)
    deadline = _parse_date(job.deadline)
    if posted and (today - posted).days > 120:
        return f"Posting is older than 4 months ({job.posted_date})."
    if deadline and (today - deadline).days > 21:
        return f"Application deadline is more than 3 weeks overdue ({job.deadline})."
    if language_risk <= -25 and language_policy["exclude_if_mandatory_unmatched"]:
        return "Mandatory language requirement appears incompatible."
    if settings["remote_policy"] == "required" and not _is_remote_or_hybrid(job) and _is_explicitly_onsite(job):
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
        reasons.append("Technical overlap: " + ", ".join(technical_matches[:8]) + ".")
    if components["module_match"] > 0:
        reasons.append("Relevant domain/module exposure: " + ", ".join(module_matches[:8]) + ".")
    if components["contract_fit"] > 0:
        reasons.append("Employment or contract format appears aligned with preferences.")
    if components["role_interest_fit"] > 0:
        reasons.append("Role text overlaps with configured interests or target roles.")
    if components["location_fit"] > 0:
        reasons.append("Location or remote setup appears potentially workable.")


def _add_concerns(
    job: Job,
    components: dict[str, int],
    text: str,
    concerns: list[str],
    missing_required_rules: list[str],
    settings: dict[str, Any],
) -> None:
    if components["language_risk"] < 0:
        concerns.append("Language requirement may need manual confirmation.")
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


def _recommended_angle(category: str, matched_keywords: list[str], review_trigger_labels: list[str]) -> str:
    if category == "excluded":
        return "Do not prioritize unless manual review overturns the exclusion."
    if matched_keywords:
        return "Lead with " + ", ".join(matched_keywords[:5]) + " and concrete relevant examples."
    if review_trigger_labels:
        return "Review the flagged caveats and keep positioning aligned with verified experience."
    return "Review manually and keep the application concise."


def _deterministic_confidence(
    category: str, total_score: int, concerns: list[str], review_triggers: list[str], source_confidence: str
) -> str:
    if category == "excluded" or total_score < 35:
        return "low"
    if review_triggers or source_confidence in {"low", "unknown"}:
        return "medium"
    if category == "strong" and not concerns:
        return "high"
    return "medium"


def _matching_review_rules(text: str, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [rule for rule in rules if any(_term_matches(text, term) for term in rule["terms"])]


def _review_concerns(profile: dict[str, Any], rules: list[dict[str, Any]]) -> list[str]:
    caveats = profile.get("skills", {}).get("caveats", {})
    caveats = caveats if isinstance(caveats, dict) else {}
    concerns = []
    for rule in rules:
        concern = ""
        caveat_key = rule.get("caveat_key")
        if caveat_key:
            concern = str(caveats.get(caveat_key, "")).strip()
        concern = concern or str(rule.get("concern", "")).strip()
        concerns.append(concern or f"Review caveat: {rule['label']}.")
    return concerns


def _normalize_caveat_rules(value: Any) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else []
    rules: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("id") or "").strip()
        terms = _terms_from_value(item.get("terms", []))
        if not label or not terms:
            continue
        rule_id = str(item.get("id") or _slug(label)).strip()
        rules.append(
            {
                "id": rule_id,
                "label": label,
                "terms": terms,
                "caveat_key": str(item.get("caveat_key", "")).strip(),
                "concern": str(item.get("concern", "")).strip(),
                "ai_review": _bool_value(item.get("ai_review"), True),
            }
        )
    return rules


def _legacy_caveat_rules(profile: dict[str, Any]) -> list[dict[str, Any]]:
    caveats = profile.get("skills", {}).get("caveats", {})
    caveats = caveats if isinstance(caveats, dict) else {}
    rules = []
    if caveats.get("fiori"):
        rules.append(
            {
                "id": "fiori_ui5_depth",
                "label": "Fiori/UI5 depth",
                "terms": ["fiori", "ui5", "sapui5"],
                "caveat_key": "fiori",
                "concern": "",
                "ai_review": True,
            }
        )
    if caveats.get("project_management"):
        rules.append(
            {
                "id": "project_management_scope",
                "label": "Project management scope",
                "terms": ["project manager", "transition manager", "service delivery manager"],
                "caveat_key": "project_management",
                "concern": "",
                "ai_review": True,
            }
        )
    return rules


def _required_language_terms(text: str, job: Job, allowed: list[str]) -> list[str]:
    known = _dedupe([*allowed, *LANGUAGE_NAMES, "english", "danish", "swedish", "norwegian", "spanish", "italian"])
    required = _terms_from_value(job.required_languages)
    for language in known:
        if _has_mandatory_language(text, language):
            required.append(language)
    return _dedupe(required)


def _has_mandatory_language(text: str, language: str) -> bool:
    language = re.escape(language.lower())
    return bool(
        re.search(
            rf"\bmandatory\s+{language}\b|\b{language}\s+required\b|\bfluent\s+{language}\b|\b{language}\s+mandatory\b",
            text,
        )
    )


def _language_allowed(language: str, allowed: list[str]) -> bool:
    language_text = " ".join(str(language).lower().split())
    return any(allowed_language in language_text or language_text in allowed_language for allowed_language in allowed)


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


def _bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _term_matches(text: str, term: str) -> bool:
    term = term.strip().lower()
    return bool(term and re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))


def _is_remote_or_hybrid(job: Job) -> bool:
    text = f"{job.location} {job.remote}".lower()
    return any(term in text for term in REMOTE_TERMS)


def _is_explicitly_onsite(job: Job) -> bool:
    text = f"{job.location} {job.remote} {job.description} {job.raw_text}".lower()
    return any(
        _term_matches(text, term)
        for term in [
            "onsite",
            "on-site",
            "on site",
            "office based",
            "office-based",
            "no remote",
            "not remote",
        ]
    )


def _is_permanent_role(text: str) -> bool:
    return any(_term_matches(text, term) for term in PERMANENT_TERMS)


def _has_listed_value(value: str) -> bool:
    return bool(value and value != "Not listed")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "review_rule"


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result
