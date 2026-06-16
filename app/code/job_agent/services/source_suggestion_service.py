from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from job_agent.config import ROOT, load_profile
from job_agent.llm import ExternalAgentService, LlmRequest, LlmService
from job_agent.prompt_context import APP_CONTEXT
from job_agent.services.source_disqualification_service import (
    SourceDisqualificationService,
    SourceDomainDisqualification,
)
from job_agent.services.source_registry_service import SourceRegistryService


@dataclass
class SourceSuggestion:
    name: str
    homepage_url: str = ""
    recommended_listing_url: str = ""
    why_relevant: str = ""
    expected_signal: str = ""
    visit_instructions: str = ""
    suggested_filters: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    caveats: str = ""
    priority: int = 3
    existing_source_id: str = ""
    existing_source_name: str = ""

    @property
    def source_url(self) -> str:
        return self.recommended_listing_url or self.homepage_url

    @property
    def priority_label(self) -> str:
        if self.priority <= 1:
            return "High"
        if self.priority == 2:
            return "Promising"
        if self.priority >= 5:
            return "Low"
        return "Review"

    @property
    def filters_label(self) -> str:
        return ", ".join(self.suggested_filters)

    @property
    def search_terms_label(self) -> str:
        return ", ".join(self.search_terms)

    @property
    def notes_for_source(self) -> str:
        parts = ["Suggested by profile-based source discovery."]
        if self.why_relevant:
            parts.append(f"Why: {self.why_relevant}")
        if self.expected_signal:
            parts.append(f"Expected signal: {self.expected_signal}")
        if self.visit_instructions:
            parts.append(f"Visit notes: {self.visit_instructions}")
        if self.filters_label:
            parts.append(f"Suggested filters: {self.filters_label}")
        if self.search_terms_label:
            parts.append(f"Search terms: {self.search_terms_label}")
        if self.caveats:
            parts.append(f"Caveats: {self.caveats}")
        return "\n".join(parts)


@dataclass
class SourceSuggestionResult:
    prompt: str
    raw_response: str = ""
    suggestions: list[SourceSuggestion] = field(default_factory=list)
    disqualified: list[SourceDomainDisqualification] = field(default_factory=list)
    model: str = ""
    focus: str = ""


class SourceSuggestionParseError(ValueError):
    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


class SourceSuggestionService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.registry = SourceRegistryService(self.root)
        self.disqualifications = SourceDisqualificationService(self.root)
        self.llm = LlmService(self.root)
        self.external = ExternalAgentService(self.root)

    def is_llm_configured(self) -> bool:
        return self.llm.is_configured()

    def build_prompt(self, focus: str = "") -> str:
        profile = _profile_for_prompt(load_profile(self.root))
        existing_sources = [
            {
                "name": source.name,
                "kind": source.kind,
                "status": source.status,
                "url": source.url,
                "tags": source.tags,
            }
            for source in self.registry.list_saved_sources(include_health=False, include_stats=False)
        ]
        disqualified_domains = [
            {"domain": record.domain, "reason": record.reason} for record in self.disqualifications.list_domains()
        ]
        focus_text = focus.strip() or "No extra focus was provided."
        payload = {
            "app_context": APP_CONTEXT,
            "profile": profile,
            "existing_sources": existing_sources,
            "disqualified_domains": disqualified_domains,
            "extra_focus": focus_text,
        }
        return (
            "You are helping a human configure job sources for Job Agent.\n\n"
            "Job Agent is a local-first preparation tool. It may discover public job postings, score them, "
            "and prepare review material. It must not submit applications, create accounts, log in, bypass captcha "
            "or bot protection, upload CVs, send emails, inspect hidden endpoints, or automate protected pages.\n\n"
            "Task:\n"
            "- Suggest 6 to 10 job boards, recruiter job pages, or company career search pages that fit the profile.\n"
            "- Prefer recurring job-result pages over one-off postings.\n"
            "- Prefer broad source URLs that can reveal adjacent or unexpected good-fit roles. Do not save URLs narrowed "
            "to one skill, one work mode, or one location unless the site requires a minimal category to show jobs.\n"
            "- Do not use profile job preferences such as remote/onsite, availability, thresholds, score settings, or "
            "preferred work mode as source filters. Use the professional profile only to judge broad source relevance.\n"
            "- If the profile mentions a specific technology, keep source discovery at the broader role-family level and "
            "put narrow terms only in optional search_terms.\n"
            "- Avoid duplicates and regional overlaps of existing sources; prefer one root domain unless the source is a "
            "materially different platform or business unit.\n"
            "- Never suggest disqualified domains, discontinued job boards, or domains listed in disqualified_domains.\n"
            "- For each source, guide a non-technical user to visit the site and find the actual job listing page.\n"
            "- If useful filters can be pre-applied in the browser, describe the exact filters or search terms to try.\n"
            "- Prefer a public jobs/search listing URL when you know it. Keep recommended_listing_url broad and avoid "
            "pre-applied keyword, remote, country, or location filters; put those ideas in suggested_filters instead.\n"
            "- If you do not know the exact jobs/search page, use the safest homepage or careers page and explain how "
            "the user should navigate to the listing URL before setup starts.\n"
            "- Flag login-only, account-gated, captcha-heavy, or apply-form-only sites as manual-review caveats.\n\n"
            "Return only strict JSON with this schema:\n"
            "{\n"
            '  "sources": [\n'
            "    {\n"
            '      "name": "Board or recruiter name",\n'
            '      "homepage_url": "https://example.com",\n'
            '      "recommended_listing_url": "https://example.com/jobs",\n'
            '      "why_relevant": "Short profile-specific reason",\n'
            '      "expected_signal": "What useful roles this source is likely to contain",\n'
            '      "visit_instructions": "Human steps to reach the filtered job posting page",\n'
            '      "suggested_filters": ["Contract", "Role family"],\n'
            '      "search_terms": ["profile-derived role", "profile-derived skill"],\n'
            '      "caveats": "Any login, region, seniority, freshness, or automation caution",\n'
            '      "priority": 1\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Context JSON:\n"
            f"{json.dumps(payload, indent=2, ensure_ascii=False, default=str)}"
        )

    def suggest_with_llm(self, focus: str = "", llm_model: str = "") -> SourceSuggestionResult:
        prompt = self.build_prompt(focus)
        if not self.llm.is_configured():
            raise RuntimeError("ANTHROPIC_API_KEY is missing or placeholder. Use the other-AI prompt instead.")
        completion = self.llm.complete(
            prompt,
            max_tokens=3600,
            purpose="source_suggestion",
            run_id="manual",
            model=llm_model,
        )
        try:
            parsed = self.parse_response_with_disqualifications(
                completion.text,
                repair_callback=lambda raw_json, error: (
                    self.llm.complete(
                        _json_repair_prompt(raw_json, error),
                        max_tokens=4200,
                        purpose="source_suggestion_repair",
                        run_id="manual",
                        model=llm_model,
                    ).text
                ),
            )
        except ValueError as exc:
            raise SourceSuggestionParseError(str(exc), completion.text) from exc
        return SourceSuggestionResult(
            prompt=prompt,
            raw_response=completion.text,
            suggestions=parsed.suggestions,
            disqualified=parsed.disqualified,
            model=completion.model,
            focus=focus,
        )

    def prepare_external(self, focus: str = ""):
        prompt = self.build_prompt(focus)
        return self.external.prepare(
            LlmRequest(prompt=prompt, max_tokens=3600, purpose="source_suggestion", run_id="manual"),
            title="Suggest job sources",
            instructions=(
                "Paste this prompt into an external agent. Paste back the full JSON response; "
                "the app will turn it into source cards you can review and save."
            ),
            metadata={"focus": focus},
        )

    def apply_external_response(self, interaction_id: str, response_text: str) -> SourceSuggestionResult:
        interaction = self.external.load(interaction_id)
        if interaction.request.purpose != "source_suggestion":
            raise ValueError("External-agent response does not belong to source suggestions.")
        completion = self.external.complete(interaction_id, response_text)
        parsed = self.parse_response_with_disqualifications(completion.text)
        return SourceSuggestionResult(
            prompt=interaction.request.prompt,
            raw_response=completion.text,
            suggestions=parsed.suggestions,
            disqualified=parsed.disqualified,
            model=completion.model,
            focus=str(interaction.metadata.get("focus") or ""),
        )

    def load_external_result(self, interaction_id: str) -> SourceSuggestionResult:
        interaction = self.external.load(interaction_id)
        if interaction.request.purpose != "source_suggestion":
            raise ValueError("External-agent response does not belong to source suggestions.")
        if interaction.status != "completed":
            raise ValueError("External-agent source suggestion response has not been applied yet.")
        parsed = self.parse_response_with_disqualifications(interaction.response_text)
        return SourceSuggestionResult(
            prompt=interaction.request.prompt,
            raw_response=interaction.response_text,
            suggestions=parsed.suggestions,
            disqualified=parsed.disqualified,
            model="external-agent",
            focus=str(interaction.metadata.get("focus") or ""),
        )

    def parse_response(self, raw_response: str, *, repair_callback=None) -> list[SourceSuggestion]:
        return self.parse_response_with_disqualifications(raw_response, repair_callback=repair_callback).suggestions

    def parse_response_with_disqualifications(
        self,
        raw_response: str,
        *,
        repair_callback=None,
    ) -> SourceSuggestionResult:
        data = _load_json_response(raw_response, repair_callback=repair_callback)
        items = data.get("sources") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError('LLM response must be a JSON object with a "sources" list.')
        suggestions = [_suggestion_from_mapping(item) for item in items if isinstance(item, dict)]
        suggestions = [suggestion for suggestion in suggestions if suggestion.name and suggestion.source_url]
        accepted: list[SourceSuggestion] = []
        disqualified: list[SourceDomainDisqualification] = []
        for suggestion in suggestions:
            disqualification = self.disqualifications.disqualification_for_suggestion(
                name=suggestion.name,
                url=suggestion.source_url,
            )
            if disqualification:
                disqualified.append(disqualification)
                continue
            accepted.append(suggestion)
        suggestions = accepted
        if not suggestions:
            detail = ""
            if disqualified:
                domains = ", ".join(record.domain for record in disqualified if record.domain)
                detail = f" {len(disqualified)} suggestion(s) were disqualified: {domains}."
            raise ValueError(f"No usable source suggestions were found in the response.{detail}")
        return SourceSuggestionResult(
            prompt="",
            raw_response=raw_response,
            suggestions=suggestions,
            disqualified=disqualified,
        )

    def annotate_existing(self, suggestions: list[SourceSuggestion]) -> list[SourceSuggestion]:
        for suggestion in suggestions:
            existing = self.registry.find_source_by_url(suggestion.source_url)
            if not existing:
                existing = self.registry.find_source_by_domain(suggestion.source_url)
            if existing:
                suggestion.existing_source_id = existing.id
                suggestion.existing_source_name = existing.name
        return suggestions

    def list_disqualified_domains(self) -> list[SourceDomainDisqualification]:
        return self.disqualifications.list_domains()

    def disqualify_domain(self, domain_or_url: str, *, reason: str = "") -> SourceDomainDisqualification:
        return self.disqualifications.add_domain(domain_or_url, reason=reason, source="manual")


def _profile_for_prompt(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "contact": _pick_mapping(
            profile.get("contact"),
            ["title", "location", "city", "country", "linkedin", "professional_links"],
        ),
        "skills": profile.get("skills", {}),
        "experience_level": profile.get("experience_level", {}),
        "experience": _compact_experience(profile.get("experience")),
        "canonical_cv_excerpt": _truncate(str(profile.get("canonical_cv") or ""), 6000),
    }


def _compact_experience(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    compacted: list[dict[str, Any]] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "company": item.get("company", ""),
                "role": item.get("role", ""),
                "highlights": item.get("highlights", [])[:4] if isinstance(item.get("highlights"), list) else [],
                "keywords": item.get("keywords", [])[:16] if isinstance(item.get("keywords"), list) else [],
            }
        )
    return compacted


def _pick_mapping(value: Any, keys: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value.get(key, "") for key in keys if value.get(key)}


def _truncate(value: str, max_length: int) -> str:
    text = value.strip()
    if len(text) <= max_length:
        return text
    return f"{text[:max_length].rstrip()}\n...[truncated]"


def _load_json_response(raw_response: str, *, repair_callback=None) -> Any:
    text = raw_response.strip()
    if not text:
        raise ValueError("Paste a JSON response before parsing suggestions.")
    last_error: json.JSONDecodeError | None = None
    last_candidate = ""
    for candidate in _json_candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            last_candidate = candidate
    if repair_callback and last_candidate and last_error:
        repaired = repair_callback(last_candidate, last_error)
        try:
            return _load_json_response(repaired)
        except ValueError as exc:
            raise ValueError(
                "Could not parse JSON source suggestions after repair: "
                f"{_json_error_message(last_error)}; repair result: {exc}"
            ) from exc
    if last_error:
        raise ValueError(f"Could not parse JSON source suggestions: {_json_error_message(last_error)}") from last_error
    raise ValueError("Could not find JSON in the LLM response.")


def _json_candidates(text: str) -> list[str]:
    candidates = [text]
    fenced = _extract_fenced_json(text)
    if fenced:
        candidates.append(fenced)
    start = min([index for index in [text.find("{"), text.find("[")] if index >= 0], default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _json_error_message(error: json.JSONDecodeError) -> str:
    return f"{error.msg} at line {error.lineno}, column {error.colno}"


def _json_repair_prompt(raw_json: str, error: json.JSONDecodeError) -> str:
    return f"""Return only corrected valid JSON.

Fix JSON syntax only. Preserve the same source suggestions, field names, values, and ordering. Do not add new sources. Do not explain.

Original parser error: {_json_error_message(error)}.

Malformed JSON:
{raw_json}
"""


def _extract_fenced_json(text: str) -> str:
    marker = "```"
    first = text.find(marker)
    if first < 0:
        return ""
    second = text.find(marker, first + len(marker))
    if second < 0:
        return ""
    body = text[first + len(marker) : second].strip()
    if body.lower().startswith("json"):
        body = body[4:].strip()
    return body


def _suggestion_from_mapping(data: dict[str, Any]) -> SourceSuggestion:
    return SourceSuggestion(
        name=_text(data.get("name")),
        homepage_url=_text(data.get("homepage_url") or data.get("home_url")),
        recommended_listing_url=_text(
            data.get("recommended_listing_url")
            or data.get("listing_url")
            or data.get("jobs_url")
            or data.get("source_url")
        ),
        why_relevant=_text(data.get("why_relevant") or data.get("reason")),
        expected_signal=_text(data.get("expected_signal")),
        visit_instructions=_text(data.get("visit_instructions") or data.get("instructions")),
        suggested_filters=_string_list(data.get("suggested_filters") or data.get("filters")),
        search_terms=_string_list(data.get("search_terms")),
        caveats=_text(data.get("caveats")),
        priority=_priority(data.get("priority")),
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _priority(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 3
    return min(max(number, 1), 5)
