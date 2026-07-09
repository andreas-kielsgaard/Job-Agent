from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .models import Job, MatchResult

LANGUAGE_NAMES = ("dutch", "french", "portuguese", "german")
REMOTE_TERMS = ("remote", "hybrid", "work from home", "wfh")
PERMANENT_TERMS = ("permanent", "permanent employee", "full-time employee", "perm role")
RULE_MODES = {"bonus", "required"}
KEYWORD_RULE_MODES = {"main", "bonus", "detractor"}
REMOTE_POLICIES = {"required", "strong_preference", "slight_preference", "neutral"}
PERMANENT_POLICIES = {"exclude", "penalize", "ignore"}
CONDITION_PREFERENCES = {"required", "preferred", "not_preferred", "not_important", "excluded"}
REMOTE_CONDITION_KEYS = ("remote", "hybrid", "onsite", "unknown")
EMPLOYMENT_TYPE_KEYS = ("contract", "employed", "unknown")

DEFAULT_MATCH_ENGINE_CONFIG: dict[str, Any] = {
    "remote_policy": "neutral",
    "permanent_policy": "ignore",
    "permanent_penalty": -25,
    "technical_cap": 55,
    "module_cap": 25,
    "keyword_groups": [],
    "technical_keyword_groups": [],
    "module_keyword_groups": [],
    "contract_keyword_groups": [],
}

DEFAULT_EMPLOYMENT_CONDITIONS_CONFIG: dict[str, Any] = {
    "employment_type": {
        "contract": "not_important",
        "employed": "not_important",
        "unknown": "not_important",
    },
    "remote": {
        "remote": "not_important",
        "hybrid": "not_important",
        "onsite": "not_important",
        "unknown": "not_important",
    },
    "locations": [],
    "contract_length": [],
    "compensation": [],
    "languages": [],
}

DEFAULT_AI_REVIEW_POLICY: dict[str, Any] = {
    "min_score": 35,
    "evaluate_categories": ["strong", "exploratory"],
    "trigger_on_highlights": True,
    "trigger_on_review_triggers": True,
    "trigger_on_low_source_confidence": True,
    "evaluate_excluded_with_triggers": False,
}


@dataclass(frozen=True)
class MatchmakingSettings:
    match_engine: dict[str, Any]
    employment_conditions: dict[str, Any]
    language_policy: dict[str, Any]
    match_review: dict[str, Any]


def matchmaking_settings_from_profile(profile: dict[str, Any]) -> MatchmakingSettings:
    return MatchmakingSettings(
        match_engine=match_engine_config_from_profile(profile),
        employment_conditions=employment_conditions_config_from_profile(profile),
        language_policy=normalize_language_policy(profile),
        match_review=normalize_match_review_config(profile),
    )


def matchmaking_settings_from_parts(
    profile: dict[str, Any],
    *,
    match_engine: dict[str, Any] | None = None,
    employment_conditions: dict[str, Any] | None = None,
) -> MatchmakingSettings:
    return MatchmakingSettings(
        match_engine=normalize_match_engine_config(
            match_engine if match_engine is not None else profile.get("match_engine", {})
        ),
        employment_conditions=normalize_employment_conditions_config(
            employment_conditions if employment_conditions is not None else profile.get("employment_conditions", {})
        ),
        language_policy=normalize_language_policy(profile),
        match_review=normalize_match_review_config(profile),
    )


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
    settings["keyword_groups"] = _normalize_keyword_groups(config.get("keyword_groups", []))
    for key in ["technical_keyword_groups", "module_keyword_groups", "contract_keyword_groups"]:
        if key in config:
            settings[key] = _normalize_rules(config.get(key), [])
    return settings


def match_engine_config_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return normalize_match_engine_config(profile.get("match_engine", {}))


def default_employment_conditions_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_EMPLOYMENT_CONDITIONS_CONFIG)


def normalize_employment_conditions_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config if isinstance(config, dict) else {}
    settings = default_employment_conditions_config()
    employment_type = config.get("employment_type") if isinstance(config.get("employment_type"), dict) else {}
    remote = config.get("remote") if isinstance(config.get("remote"), dict) else {}
    settings["employment_type"] = {
        key: _condition_preference(employment_type.get(key), settings["employment_type"][key])
        for key in EMPLOYMENT_TYPE_KEYS
    }
    settings["remote"] = {
        key: _condition_preference(remote.get(key), settings["remote"][key]) for key in REMOTE_CONDITION_KEYS
    }
    settings["locations"] = _normalize_condition_rows(config.get("locations", []), with_kind=True)
    settings["contract_length"] = _normalize_condition_rows(config.get("contract_length", []))
    settings["compensation"] = _normalize_compensation_rows(config.get("compensation", []))
    settings["languages"] = _normalize_condition_rows(config.get("languages", []))
    return settings


def employment_conditions_config_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return normalize_employment_conditions_config(profile.get("employment_conditions", {}))


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


def evaluate_employment_conditions(job: Job, settings: dict[str, Any]) -> dict[str, Any]:
    text = _job_text(job)
    location_text = _location_condition_text(job)
    employment_type = _employment_type(job, text)
    remote_type = _remote_condition(job)
    values = {
        "employment_type": employment_type,
        "remote": remote_type,
        "locations": _matching_location_labels(job, settings["locations"]),
        "languages": _required_language_terms(text, job, []),
        "compensation": _compensation_value(job.rate),
        "contract_length": job.contract_duration if _has_listed_value(job.contract_duration) else "",
    }
    exclusions: list[str] = []
    preferences: list[str] = []

    _evaluate_choice_condition("Employment type", employment_type, settings["employment_type"], exclusions, preferences)
    _evaluate_choice_condition("Remote setup", remote_type, settings["remote"], exclusions, preferences)
    _evaluate_tag_conditions("Location", location_text, settings["locations"], exclusions, preferences)
    _evaluate_tag_conditions(
        "Contract length", job.contract_duration, settings["contract_length"], exclusions, preferences
    )
    _evaluate_language_conditions(text, job, settings["languages"], exclusions, preferences)
    _evaluate_compensation_conditions(job.rate, settings["compensation"], exclusions, preferences)

    return {"exclusions": exclusions, "preferences": preferences, "values": values}


def score_job(
    job: Job,
    profile: dict,
    today: date | None = None,
    matchmaking_settings: MatchmakingSettings | None = None,
) -> MatchResult:
    today = today or date.today()
    matchmaking_settings = matchmaking_settings or matchmaking_settings_from_profile(profile)
    settings = matchmaking_settings.match_engine
    review_settings = matchmaking_settings.match_review
    language_policy = matchmaking_settings.language_policy
    employment_conditions = matchmaking_settings.employment_conditions
    text = _job_text(job)
    condition_evaluation = evaluate_employment_conditions(job, employment_conditions)
    if settings["keyword_groups"]:
        (
            components,
            matched_keywords,
            missing_required_rules,
            proficiency_concerns,
            technical_matches,
            module_matches,
        ) = _score_proficiency_rules(text, settings["keyword_groups"])
    else:
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
        proficiency_concerns = []
    if settings["keyword_groups"]:
        language_risk = _language_risk(text, job, language_policy) if language_policy["mode"] == "configured" else 0
        components["language_risk"] = language_risk
        components["freshness_risk"] = _freshness_risk(job, today)
    else:
        language_risk = _language_risk(text, job, language_policy)
    review_matches = _matching_review_rules(text, review_settings["caveat_rules"])
    review_triggers = [rule["id"] for rule in review_matches if rule["ai_review"]]
    review_trigger_labels = [rule["label"] for rule in review_matches if rule["ai_review"]]
    reasons: list[str] = []
    concerns: list[str] = []
    missing_information: list[str] = []

    _add_positive_reasons(components, technical_matches, module_matches, reasons)
    if settings["keyword_groups"]:
        _add_proficiency_reasons(components, matched_keywords, reasons)
    concerns.extend(_review_concerns(profile, review_matches))
    concerns.extend(proficiency_concerns)
    _add_concerns(job, components, text, concerns, missing_required_rules, settings)
    _add_missing(job, missing_information)

    exclusion_reason = _exclusion_reason(
        job, today, text, settings, missing_required_rules, language_risk, language_policy
    )
    raw_score = sum(components.values())
    total_score = max(0, raw_score)
    if not settings["keyword_groups"]:
        total_score = min(100, total_score)

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
        condition_exclusions=_dedupe(condition_evaluation["exclusions"]),
        condition_preferences=_dedupe(condition_evaluation["preferences"]),
        condition_values=condition_evaluation["values"],
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


def _score_proficiency_rules(
    text: str, rules: list[dict[str, Any]]
) -> tuple[dict[str, int], list[str], list[str], list[str], list[str], list[str]]:
    main_scores: list[int] = []
    bonus_scores: list[int] = []
    detractor_scores: list[int] = []
    matched: list[str] = []
    main_matches: list[str] = []
    bonus_matches: list[str] = []
    detractor_matches: list[str] = []
    concerns: list[str] = []
    mentioned_years = _mentioned_years(text)

    for rule in rules:
        label = str(rule.get("label", "")).strip()
        terms = [str(term).strip().lower() for term in rule.get("terms", []) if str(term).strip()]
        if not label or not terms:
            continue
        if not any(_term_matches(text, term) for term in terms):
            continue
        mode = str(rule.get("mode") or "main")
        value = _int_value(rule.get("proficiency"), _int_value(rule.get("score"), 0))
        matched.append(label)
        if mode == "main":
            main_scores.append(value)
            main_matches.append(label)
            configured_years = _int_value(rule.get("years"), 0)
            if configured_years and mentioned_years and mentioned_years > configured_years:
                concerns.append(
                    f"{label} is configured with {configured_years} years; posting mentions {mentioned_years}+ years."
                )
        elif mode == "bonus":
            bonus_scores.append(max(0, value))
            bonus_matches.append(label)
        elif mode == "detractor":
            detractor_scores.append(abs(value))
            detractor_matches.append(label)

    main_average = round(sum(main_scores) / len(main_scores)) if main_scores else 0
    bonus_boost = max(bonus_scores) if bonus_scores else 0
    detractor_penalty = max(detractor_scores) if detractor_scores else 0
    components = {
        "main_proficiency": main_average,
        "bonus_boost": bonus_boost,
        "detractor_penalty": -detractor_penalty,
    }
    if not main_matches and (bonus_matches or detractor_matches):
        concerns.append("Only bonus or detractor keyword rules matched; no main proficiency rule matched.")
    if detractor_matches:
        concerns.append("Matched detractor signal: " + ", ".join(detractor_matches[:5]) + ".")
    reasons_as_technical = main_matches + bonus_matches
    reasons_as_modules: list[str] = []
    return components, _dedupe(matched), [], concerns, reasons_as_technical, reasons_as_modules


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
    if components.get("technical_match", 0) > 0:
        reasons.append("Technical overlap: " + ", ".join(technical_matches[:8]) + ".")
    if components.get("module_match", 0) > 0:
        reasons.append("Relevant domain/module exposure: " + ", ".join(module_matches[:8]) + ".")
    if components.get("contract_fit", 0) > 0:
        reasons.append("Employment or contract format appears aligned with preferences.")
    if components.get("role_interest_fit", 0) > 0:
        reasons.append("Role text overlaps with configured interests or target roles.")
    if components.get("location_fit", 0) > 0:
        reasons.append("Location or remote setup appears potentially workable.")


def _add_proficiency_reasons(components: dict[str, int], matched_keywords: list[str], reasons: list[str]) -> None:
    if components.get("main_proficiency", 0) > 0:
        reasons.append("Main proficiency match: " + ", ".join(matched_keywords[:8]) + ".")
    if components.get("bonus_boost", 0) > 0:
        reasons.append(f"Best matched bonus boost adds {components['bonus_boost']}%.")
    if components.get("detractor_penalty", 0) < 0:
        reasons.append(f"Highest matched detractor subtracts {abs(components['detractor_penalty'])}%.")


def _add_concerns(
    job: Job,
    components: dict[str, int],
    text: str,
    concerns: list[str],
    missing_required_rules: list[str],
    settings: dict[str, Any],
) -> None:
    if components.get("language_risk", 0) < 0:
        concerns.append("Language requirement may need manual confirmation.")
    if components.get("rate_visibility_or_rate_fit", 0) < 0:
        concerns.append("Rate or salary is not listed.")
    if components.get("contract_fit", 0) < 0 and _is_permanent_role(text):
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


def _evaluate_choice_condition(
    label: str,
    actual: str,
    preferences_by_value: dict[str, str],
    exclusions: list[str],
    preferences: list[str],
) -> None:
    required = [key for key, value in preferences_by_value.items() if value == "required"]
    preferred = [key for key, value in preferences_by_value.items() if value == "preferred"]
    not_preferred = [key for key, value in preferences_by_value.items() if value == "not_preferred"]
    excluded = [key for key, value in preferences_by_value.items() if value == "excluded"]
    if actual in excluded:
        exclusions.append(f"{label} is {actual}, which is excluded.")
    if required and actual not in required:
        exclusions.append(f"{label} must be {', '.join(required)}, but posting appears to be {actual}.")
    if preferred and actual not in preferred:
        preferences.append(f"{label} preference is {', '.join(preferred)}; posting appears to be {actual}.")
    if actual in not_preferred:
        preferences.append(f"{label} is {actual}, which is marked not preferred.")


def _evaluate_tag_conditions(
    label: str,
    value: str,
    rows: list[dict[str, str]],
    exclusions: list[str],
    preferences: list[str],
) -> None:
    text = normalize_for_condition(value)
    if not rows:
        return
    required_rows = [row for row in rows if row["preference"] == "required"]
    if required_rows and not any(_condition_term_matches(text, row["label"]) for row in required_rows):
        exclusions.append(f"{label} must include one of: {', '.join(row['label'] for row in required_rows[:5])}.")
    preferred_rows = [row for row in rows if row["preference"] == "preferred"]
    if preferred_rows and not any(_condition_term_matches(text, row["label"]) for row in preferred_rows):
        preferences.append(
            f"{label} preference is not visible: {', '.join(row['label'] for row in preferred_rows[:5])}."
        )
    for row in rows:
        row_label = row["label"]
        preference = row["preference"]
        matches = _condition_term_matches(text, row_label)
        if preference == "excluded" and matches:
            exclusions.append(f"{label} matches excluded value {row_label}.")
        elif preference == "not_preferred" and matches:
            preferences.append(f"{label} matches not-preferred value {row_label}.")


def _evaluate_language_conditions(
    text: str,
    job: Job,
    rows: list[dict[str, str]],
    exclusions: list[str],
    preferences: list[str],
) -> None:
    if not rows:
        return
    row_labels = [row["label"] for row in rows]
    required_languages = _required_language_terms(text, job, row_labels)
    if not required_languages:
        return
    compatible = [
        row["label"]
        for row in rows
        if row["preference"] in {"required", "preferred", "not_important", "not_preferred"}
        and any(_language_allowed(language, [row["label"]]) for language in required_languages)
    ]
    excluded_matches = [
        row["label"]
        for row in rows
        if row["preference"] == "excluded"
        and any(_language_allowed(language, [row["label"]]) for language in required_languages)
    ]
    if excluded_matches:
        exclusions.append("Required language matches excluded language: " + ", ".join(excluded_matches[:5]) + ".")
    if not compatible and not excluded_matches:
        exclusions.append("Mandatory language requirement is not covered by configured language options.")
    not_preferred_matches = [
        row["label"]
        for row in rows
        if row["preference"] == "not_preferred"
        and any(_language_allowed(language, [row["label"]]) for language in required_languages)
    ]
    if not_preferred_matches:
        preferences.append(
            "Required language matches not-preferred language: " + ", ".join(not_preferred_matches[:5]) + "."
        )


def _evaluate_compensation_conditions(
    rate: str,
    rows: list[dict[str, Any]],
    exclusions: list[str],
    preferences: list[str],
) -> None:
    if not rows:
        return
    compensation = _compensation_value(rate)
    if not compensation:
        if any(row["preference"] == "required" for row in rows):
            exclusions.append("Compensation is required by employment conditions but is not listed.")
        elif any(row["preference"] == "preferred" for row in rows):
            preferences.append("Preferred compensation information is not listed.")
        return
    for row in rows:
        minimum = int(row.get("minimum") or 0)
        if minimum <= 0:
            continue
        period = str(row.get("period") or "").lower()
        if period and compensation.get("period") != period:
            continue
        amount = int(compensation.get("amount") or 0)
        if amount >= minimum:
            continue
        if row["preference"] == "required":
            exclusions.append(f"Compensation {amount} {period} is below required minimum {minimum}.")
        elif row["preference"] == "preferred":
            preferences.append(f"Compensation {amount} {period} is below preferred minimum {minimum}.")


def _employment_type(job: Job, text: str) -> str:
    workload = normalize_for_condition(job.workload)
    if any(_term_matches(text, term) for term in ["contract", "contractor", "freelance", "freelancer", "temporary"]):
        return "contract"
    if any(_term_matches(workload, term) for term in ["contract", "contractor", "freelance"]):
        return "contract"
    if _is_permanent_role(text) or any(
        _term_matches(workload, term) for term in ["employee", "employed", "full time", "full-time", "permanent"]
    ):
        return "employed"
    return "unknown"


def _remote_condition(job: Job) -> str:
    text = normalize_for_condition(f"{job.remote} {job.location} {job.description} {job.raw_text}")
    if _term_matches(text, "hybrid"):
        return "hybrid"
    if any(_term_matches(text, term) for term in ["remote", "fully remote", "100 remote", "work from home", "wfh"]):
        return "remote"
    if _is_explicitly_onsite(job):
        return "onsite"
    return "unknown"


def _matching_location_labels(job: Job, rows: list[dict[str, str]]) -> list[str]:
    text = _location_condition_text(job)
    return [row["label"] for row in rows if _condition_term_matches(text, row["label"])]


def _location_condition_text(job: Job) -> str:
    return normalize_for_condition(f"{job.location} {job.remote} {job.description}")


def _condition_term_matches(text: str, term: str) -> bool:
    normalized_term = normalize_for_condition(term)
    if not normalized_term:
        return False
    if normalized_term in {"eu", "europe", "european union"}:
        return any(_term_matches(text, marker) for marker in ["eu", "europe", "european union", "emea"])
    return normalized_term in text or _term_matches(text, normalized_term)


def _compensation_value(rate: str) -> dict[str, Any]:
    text = normalize_for_condition(rate)
    if not text or "not listed" in text:
        return {}
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(k\b)?", text)
    if not match:
        return {}
    amount = float(match.group(1).replace(",", "."))
    if match.group(2):
        amount *= 1000
    period = "daily"
    if any(marker in text for marker in ["hour", "/h", " per h"]):
        period = "hourly"
    elif any(marker in text for marker in ["month", "/mo", "monthly"]):
        period = "monthly"
    elif any(marker in text for marker in ["year", "annual", "annum", "/yr", " p.a", " pa"]):
        period = "yearly"
    return {"amount": round(amount), "period": period}


def normalize_for_condition(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


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
    allowed_languages = [" ".join(str(allowed_language).lower().split()) for allowed_language in allowed]
    return any(
        allowed_language in language_text or language_text in allowed_language for allowed_language in allowed_languages
    )


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


def _mentioned_years(text: str) -> int:
    values: list[int] = []
    for match in re.finditer(r"\b(\d{1,2})\+?\s*(?:years?|yrs?)\b", text):
        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 40)
        window = text[start:end]
        if any(term in window for term in ["experience", "experienced", "hands-on", "senior", "consultant"]):
            values.append(int(match.group(1)))
    return max(values) if values else 0


def _normalize_keyword_groups(value: Any) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else []
    rules: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        terms = _terms_from_value(item.get("terms", []))
        mode = _choice(str(item.get("mode", "")).lower(), KEYWORD_RULE_MODES, "main")
        default_value = 100 if mode == "main" else 0
        proficiency = _int_value(item.get("proficiency", item.get("score")), default_value)
        proficiency = -abs(proficiency) if mode == "detractor" else max(0, proficiency)
        years = max(0, _int_value(item.get("years"), 0))
        if label and terms:
            rules.append(
                {
                    "label": label,
                    "terms": terms,
                    "proficiency": proficiency,
                    "mode": mode,
                    "years": years,
                }
            )
    return rules


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


def _normalize_condition_rows(value: Any, *, with_kind: bool = False) -> list[dict[str, str]]:
    source = value if isinstance(value, list) else []
    rows: list[dict[str, str]] = []
    for item in source:
        if isinstance(item, str):
            label = item.strip()
            preference = "preferred"
            kind = "region"
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("value") or item.get("name") or "").strip()
            preference = _condition_preference(item.get("preference"), "preferred")
            kind = str(item.get("kind") or "region").strip().lower()
        else:
            continue
        if not label:
            continue
        row = {"label": label, "preference": preference}
        if with_kind:
            row["kind"] = kind if kind in {"country", "city", "region"} else "region"
        rows.append(row)
    return rows


def _normalize_compensation_rows(value: Any) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else []
    rows: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        period = str(item.get("period") or "").strip().lower()
        if period not in {"hourly", "daily", "monthly", "yearly"}:
            continue
        minimum = max(0, _int_value(item.get("minimum") or item.get("amount"), 0))
        preference = _condition_preference(item.get("preference"), "preferred")
        rows.append({"period": period, "minimum": minimum, "preference": preference})
    return rows


def _terms_from_value(value: Any) -> list[str]:
    parts = value if isinstance(value, list) else re.split(r"[\n,]+", str(value or ""))
    return _dedupe([str(part).strip().lower() for part in parts if str(part).strip()])


def _choice(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _condition_preference(value: Any, default: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"not_preferred": "not_preferred", "notimportant": "not_important", "not_important": "not_important"}
    text = aliases.get(text, text)
    return text if text in CONDITION_PREFERENCES else default


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
