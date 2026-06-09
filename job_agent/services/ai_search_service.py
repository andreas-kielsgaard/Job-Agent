from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.models import Job, MatchResult, normalize_text

from job_agent.llm import LlmService


@dataclass
class AiSearchEvaluation:
    status: str = "missing"
    summary: str = ""
    recommended_angle: str = ""
    fit_confidence: str = ""
    risk_flags: list[str] = field(default_factory=list)
    key_profile_evidence: list[str] = field(default_factory=list)
    should_prioritize: bool = False
    model: str = ""
    error: str = ""

    def to_index_fields(self) -> dict[str, Any]:
        return {
            "ai_evaluation_status": self.status,
            "ai_summary": self.summary,
            "ai_recommended_angle": self.recommended_angle,
            "ai_fit_confidence": self.fit_confidence,
            "ai_risk_flags": self.risk_flags,
            "ai_key_profile_evidence": self.key_profile_evidence,
            "ai_should_prioritize": self.should_prioritize,
            "ai_model": self.model,
            "ai_error": self.error,
        }


class AiSearchService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.llm = LlmService(root)

    def is_configured(self) -> bool:
        return self.llm.is_configured()

    def evaluate(
        self,
        job: Job,
        match: MatchResult,
        profile: dict[str, Any],
        highlight_reasons: list[str],
        *,
        run_id: str,
        stable_id: str,
    ) -> AiSearchEvaluation:
        prompt = self._build_prompt(job, match, profile, highlight_reasons)
        completion = self.llm.complete(
            prompt,
            max_tokens=900,
            purpose="ai_search_evaluation",
            run_id=run_id,
            associated_job_id=stable_id,
        )
        parsed = parse_ai_search_response(completion.text)
        parsed.status = "evaluated"
        parsed.model = completion.model
        return parsed

    def skipped(self, reason: str = "") -> AiSearchEvaluation:
        return AiSearchEvaluation(status="skipped", error=reason)

    def failed(self, error: str) -> AiSearchEvaluation:
        return AiSearchEvaluation(status="failed", error=error, model=self.llm.model_name())

    def _build_prompt(self, job: Job, match: MatchResult, profile: dict[str, Any], highlight_reasons: list[str]) -> str:
        path = self.root / "prompts" / "evaluate_job_relevance.md"
        template = path.read_text(encoding="utf-8") if path.exists() else DEFAULT_PROMPT
        return template.format(
            canonical_cv=profile.get("canonical_cv", ""),
            writing_style=profile.get("writing_style", ""),
            profile_json=json.dumps(_profile_context(profile), ensure_ascii=False, indent=2, default=str),
            job_json=json.dumps(asdict(job), ensure_ascii=False, indent=2, default=str),
            match_json=json.dumps(asdict(match), ensure_ascii=False, indent=2, default=str),
            highlight_reasons=json.dumps(highlight_reasons, ensure_ascii=False),
        )


def should_ai_evaluate_job(
    job: Job,
    match: MatchResult,
    profile: dict[str, Any],
    highlight_reasons: list[str],
) -> bool:
    if match.category == "excluded" or match.total_score < 35:
        return False
    threshold = int(profile.get("thresholds", {}).get("ai_evaluation_score", 60) or 60)
    text = normalize_text(
        " ".join([job.title, job.description, job.remote, job.location, " ".join(job.required_skills)])
    )
    ambiguity_terms = ("fiori", "ui5", "project manager", "coordinator", "language", "fullstack", "functional")
    return (
        match.category in {"strong", "exploratory"}
        or match.total_score >= threshold
        or bool(highlight_reasons)
        or any(term in text for term in ambiguity_terms)
    )


def parse_ai_search_response(text: str) -> AiSearchEvaluation:
    try:
        data = json.loads(_extract_json(text))
    except (json.JSONDecodeError, ValueError):
        return AiSearchEvaluation(
            summary=text.strip()[:700],
            recommended_angle="Review the raw AI summary manually.",
            fit_confidence="medium",
            risk_flags=["AI response was not valid JSON."],
            should_prioritize=False,
        )
    return AiSearchEvaluation(
        summary=str(data.get("summary", "")).strip(),
        recommended_angle=str(data.get("recommended_angle", "")).strip(),
        fit_confidence=_normalized_confidence(data.get("fit_confidence", "")),
        risk_flags=_list_from_value(data.get("risk_flags", []))[:6],
        key_profile_evidence=_list_from_value(data.get("key_profile_evidence", []))[:6],
        should_prioritize=bool(data.get("should_prioritize", False)),
    )


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found")
    return stripped[start : end + 1]


def _normalized_confidence(value: Any) -> str:
    text = normalize_text(value)
    return text if text in {"high", "medium", "low"} else "medium"


def _list_from_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _profile_context(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "contact": profile.get("contact", {}),
        "availability": profile.get("availability", {}),
        "location_policy": profile.get("location_policy", {}),
        "role_preferences": profile.get("role_preferences", {}),
        "skills": profile.get("skills", {}),
        "experience_level": profile.get("experience_level", {}),
        "experience": profile.get("experience", []),
    }


DEFAULT_PROMPT = """You are evaluating a SAP freelance/contract role for Andreas Kielsgaard.

Andreas is a SAP ABAP/RAP consultant with 6+ years of SAP technical delivery experience.
Use only the supplied profile/CV context. Do not invent experience.
Be precise about partial matches such as Fiori/UI5 frontend depth, project management ownership, and language constraints.
This output is for a run overview, not an application letter. Keep it concise and practical.

Canonical CV:
{canonical_cv}

Profile JSON:
{profile_json}

Job JSON:
{job_json}

Deterministic match JSON:
{match_json}

Highlight reasons:
{highlight_reasons}

Return only valid JSON with:
summary: 1-3 sentences for triage.
recommended_angle: concise positioning advice.
fit_confidence: high, medium, or low.
risk_flags: list of short risks.
key_profile_evidence: list of 2-4 profile evidence bullets.
should_prioritize: boolean.
"""
