from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from job_agent.config import ROOT
from job_agent.services.job_board_recipe_service import check_recipe_against_html, job_board_recipe_from_mapping
from job_agent.services.llm_service import LlmService

EXPECTED_ARTIFACT_FILES = [
    "summary.md",
    "selector-report.json",
    "candidate-elements.html",
    "visible-text.txt",
    "page.html",
]
CONFIDENCE_VALUES = {"low", "medium", "high"}
STRATEGIES = {"selector_based", "pattern_based", "selector_and_pattern", "not_recommended"}


class RecipeSuggestionLlmClient(Protocol):
    def suggest(self, prompt: str) -> str:
        raise NotImplementedError


class LlmServiceRecipeSuggestionClient:
    def __init__(self, root: Path = ROOT) -> None:
        self.llm = LlmService(root)

    def suggest(self, prompt: str) -> str:
        if not self.llm.is_configured():
            raise RuntimeError("ANTHROPIC_API_KEY is missing or placeholder.")
        return self.llm.complete(
            prompt,
            max_tokens=2200,
            purpose="recipe_suggestion",
            run_id="manual",
        ).text


@dataclass
class RecipeSuggestionEvidence:
    artifact_dir: Path
    source_name: str = ""
    start_url: str = ""
    warnings: list[str] = field(default_factory=list)
    evidence_summary: str = ""
    prompt_payload: dict = field(default_factory=dict)
    referenced_artifact_files: list[str] = field(default_factory=list)


@dataclass
class RecipeSuggestionResult:
    source_name: str
    start_url: str
    artifact_dir: Path
    suggested_recipe_yaml: str = ""
    explanation: str = ""
    confidence: str = "low"
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence_summary: str = ""
    selected_strategy: str = "not_recommended"
    referenced_artifact_files: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    schema_valid: bool = False


@dataclass
class RecipeRefinementAttempt:
    attempt_number: int
    suggested_recipe_yaml: str
    schema_valid: bool
    validation_errors: list[str]
    quality_status: str
    quality_warnings: list[str]
    extracted_job_count: int = 0
    useful_titles: int = 0
    generic_labels: int = 0
    unique_urls: int = 0
    average_description_length: int = 0
    revision_reason: str = ""


@dataclass
class RecipeRefinementResult:
    final_result: RecipeSuggestionResult
    attempts: list[RecipeRefinementAttempt]
    accepted: bool


def suggest_recipe_from_artifact(
    artifact_dir: Path,
    source_name: str = "",
    start_url: str = "",
    existing_recipe_path: Path | None = None,
    llm_client: RecipeSuggestionLlmClient | None = None,
    root: Path = ROOT,
) -> RecipeSuggestionResult:
    evidence = load_recipe_suggestion_evidence(
        artifact_dir,
        source_name=source_name,
        start_url=start_url,
        existing_recipe_path=existing_recipe_path,
    )
    client = llm_client or LlmServiceRecipeSuggestionClient(root)
    prompt = build_recipe_suggestion_prompt(evidence)
    raw_response = client.suggest(prompt)
    return _suggestion_result_from_response(evidence, raw_response)


def suggest_recipe_with_refinement(
    artifact_dir: Path,
    source_name: str = "",
    start_url: str = "",
    existing_recipe_path: Path | None = None,
    llm_client: RecipeSuggestionLlmClient | None = None,
    max_attempts: int = 3,
    root: Path = ROOT,
) -> RecipeRefinementResult:
    if max_attempts <= 0:
        raise ValueError("--max-attempts must be a positive integer.")

    evidence = load_recipe_suggestion_evidence(
        artifact_dir,
        source_name=source_name,
        start_url=start_url,
        existing_recipe_path=existing_recipe_path,
    )
    client = llm_client or LlmServiceRecipeSuggestionClient(root)
    attempts: list[RecipeRefinementAttempt] = []
    final_result: RecipeSuggestionResult | None = None

    prompt = build_recipe_suggestion_prompt(evidence)
    for attempt_number in range(1, max_attempts + 1):
        raw_response = client.suggest(prompt)
        result = _suggestion_result_from_response(evidence, raw_response)
        final_result = result
        attempt = evaluate_suggestion_against_artifact(result)
        attempt.attempt_number = attempt_number
        attempts.append(attempt)
        if _attempt_is_acceptable(attempt):
            break
        if attempt_number < max_attempts:
            prompt = build_recipe_refinement_prompt(evidence, attempt)

    assert final_result is not None
    accepted = bool(attempts and _attempt_is_acceptable(attempts[-1]))
    return RecipeRefinementResult(final_result=final_result, attempts=attempts, accepted=accepted)


def evaluate_suggestion_against_artifact(result: RecipeSuggestionResult) -> RecipeRefinementAttempt:
    warnings: list[str] = []
    if not result.schema_valid:
        warnings.extend(result.validation_errors)
        return RecipeRefinementAttempt(
            attempt_number=0,
            suggested_recipe_yaml=result.suggested_recipe_yaml,
            schema_valid=False,
            validation_errors=result.validation_errors,
            quality_status="poor",
            quality_warnings=warnings,
            revision_reason="Schema validation failed.",
        )

    page_path = result.artifact_dir / "page.html"
    if not page_path.exists():
        return RecipeRefinementAttempt(
            attempt_number=0,
            suggested_recipe_yaml=result.suggested_recipe_yaml,
            schema_valid=True,
            validation_errors=[],
            quality_status="poor",
            quality_warnings=["Missing local page.html; cannot validate extraction quality."],
            revision_reason="Local page.html is missing.",
        )

    try:
        data = yaml.safe_load(result.suggested_recipe_yaml) or {}
        recipe = job_board_recipe_from_mapping(data, label="suggested_recipe_yaml")
        html = page_path.read_text(encoding="utf-8", errors="replace")
        quality = check_recipe_against_html(html, result.start_url or recipe.start_url, recipe, follow_detail=False)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return RecipeRefinementAttempt(
            attempt_number=0,
            suggested_recipe_yaml=result.suggested_recipe_yaml,
            schema_valid=True,
            validation_errors=[],
            quality_status="poor",
            quality_warnings=[f"Local recipe quality check failed: {exc}"],
            revision_reason="Local extraction check failed.",
        )

    warnings.extend(quality.warnings)
    quality_status = "good"
    revision_reason = ""
    if quality.candidate_count == 0:
        quality_status = "poor"
        warnings.append("No jobs were extracted from local page.html.")
        revision_reason = "Recipe extracted no jobs from local page.html."
    elif quality.useful_title_count == 0:
        quality_status = "poor"
        warnings.append("No useful job titles were extracted.")
        revision_reason = "Recipe extracted no useful titles."
    elif quality.unique_url_count == 0:
        quality_status = "poor"
        warnings.append("No unique job URLs were extracted.")
        revision_reason = "Recipe extracted no unique URLs."
    elif quality.generic_title_count >= quality.candidate_count:
        quality_status = "poor"
        warnings.append("All extracted titles look generic.")
        revision_reason = "Recipe extracted only generic titles."
    elif quality.average_description_length < 40:
        quality_status = "warning"
        warnings.append("Average description length is low; verify the card selector captures enough text.")

    return RecipeRefinementAttempt(
        attempt_number=0,
        suggested_recipe_yaml=result.suggested_recipe_yaml,
        schema_valid=True,
        validation_errors=[],
        quality_status=quality_status,
        quality_warnings=warnings,
        extracted_job_count=quality.candidate_count,
        useful_titles=quality.useful_title_count,
        generic_labels=quality.generic_title_count,
        unique_urls=quality.unique_url_count,
        average_description_length=quality.average_description_length,
        revision_reason=revision_reason,
    )


def load_recipe_suggestion_evidence(
    artifact_dir: Path,
    *,
    source_name: str = "",
    start_url: str = "",
    existing_recipe_path: Path | None = None,
) -> RecipeSuggestionEvidence:
    artifact_dir = Path(artifact_dir)
    warnings: list[str] = []
    referenced: list[str] = []
    for name in EXPECTED_ARTIFACT_FILES:
        if (artifact_dir / name).exists():
            referenced.append(name)
        else:
            warnings.append(f"Missing artifact file: {name}")

    summary = _read_text(artifact_dir / "summary.md", 5000)
    selector_report = _read_json(artifact_dir / "selector-report.json")
    visible_text = _read_text(artifact_dir / "visible-text.txt", 5000)
    candidate_html = _read_text(artifact_dir / "candidate-elements.html", 5000)
    existing_recipe = _read_text(existing_recipe_path, 5000) if existing_recipe_path else ""
    if existing_recipe_path:
        if existing_recipe_path.exists():
            referenced.append(str(existing_recipe_path))
        else:
            warnings.append(f"Existing recipe not found: {existing_recipe_path}")

    report_url = str(selector_report.get("url") or "") if isinstance(selector_report, dict) else ""
    report_mode = str(selector_report.get("capture_mode") or "") if isinstance(selector_report, dict) else ""
    candidates = _candidate_summaries(selector_report)
    payload = {
        "source_name": source_name,
        "start_url": start_url or report_url,
        "capture_url": report_url,
        "capture_mode": report_mode,
        "top_candidates": candidates,
        "visible_text_sample": visible_text,
        "candidate_elements_sample": candidate_html,
        "summary": summary,
        "existing_recipe_yaml": existing_recipe,
        "recipe_schema_summary": _recipe_schema_summary(),
    }
    evidence_summary = _evidence_summary(report_url, report_mode, candidates, visible_text)
    return RecipeSuggestionEvidence(
        artifact_dir=artifact_dir,
        source_name=source_name or "Recipe source",
        start_url=start_url or report_url,
        warnings=warnings,
        evidence_summary=evidence_summary,
        prompt_payload=payload,
        referenced_artifact_files=referenced,
    )


def build_recipe_suggestion_prompt(evidence: RecipeSuggestionEvidence) -> str:
    return (
        "You suggest constrained Job-Agent recipe YAML from local calibration artifacts only.\n"
        "Return only strict JSON with keys: suggested_recipe_yaml, explanation, confidence, "
        "assumptions, warnings, selected_strategy.\n"
        "The YAML may use only this schema: source_name, start_url, mode, listing, accept, reject, "
        "patterns, limits, detail. Do not include Python code, browser scripts, arbitrary adapters, "
        "pagination, login/session/cookie handling, hidden endpoint assumptions, or network/API discovery.\n"
        "Use detail.follow only when the evidence clearly justifies bounded detail-page enrichment.\n"
        "Prefer selectors and regex patterns visible in the local evidence. If automation is not recommended, "
        "choose selected_strategy not_recommended and explain why.\n\n"
        f"Evidence JSON:\n{json.dumps(evidence.prompt_payload, ensure_ascii=False, indent=2)}"
    )


def build_recipe_refinement_prompt(evidence: RecipeSuggestionEvidence, attempt: RecipeRefinementAttempt) -> str:
    report = {
        "schema_valid": attempt.schema_valid,
        "validation_errors": attempt.validation_errors,
        "quality_status": attempt.quality_status,
        "quality_warnings": attempt.quality_warnings,
        "extracted_job_count": attempt.extracted_job_count,
        "useful_titles": attempt.useful_titles,
        "generic_labels": attempt.generic_labels,
        "unique_urls": attempt.unique_urls,
        "average_description_length": attempt.average_description_length,
        "revision_reason": attempt.revision_reason,
    }
    return (
        "Revise the constrained Job-Agent recipe YAML using only the local calibration evidence and "
        "the deterministic validation report below.\n"
        "Return only strict JSON with keys: suggested_recipe_yaml, explanation, confidence, "
        "assumptions, warnings, selected_strategy.\n"
        "The YAML may use only this schema: source_name, start_url, mode, listing, accept, reject, "
        "patterns, limits, detail. Do not include Python code, browser scripts, arbitrary adapters, "
        "pagination, login/session/cookie handling, hidden endpoint assumptions, or network/API discovery.\n"
        "Do not assume access to any page beyond the saved local artifact.\n\n"
        f"Evidence JSON:\n{json.dumps(evidence.prompt_payload, ensure_ascii=False, indent=2)}\n\n"
        f"Previous suggested YAML:\n{attempt.suggested_recipe_yaml}\n\n"
        f"Validation and local extraction report:\n{json.dumps(report, ensure_ascii=False, indent=2)}"
    )


def validate_suggested_recipe_yaml(value: str) -> list[str]:
    if not value.strip():
        return ["suggested_recipe_yaml is empty."]
    try:
        data = yaml.safe_load(value)
    except yaml.YAMLError as exc:
        return [f"YAML parse failed: {exc}"]
    if not isinstance(data, dict):
        return ["Suggested recipe YAML must be a mapping."]
    try:
        job_board_recipe_from_mapping(data, label="suggested_recipe_yaml")
    except ValueError as exc:
        return [str(exc)]
    return []


def _suggestion_result_from_response(evidence: RecipeSuggestionEvidence, raw_response: str) -> RecipeSuggestionResult:
    parsed = _parse_llm_json(raw_response)
    suggested_yaml = str(parsed.get("suggested_recipe_yaml") or "").strip()
    validation_errors = validate_suggested_recipe_yaml(suggested_yaml)
    warnings = list(evidence.warnings) + _list_value(parsed.get("warnings"))
    return RecipeSuggestionResult(
        source_name=evidence.source_name,
        start_url=evidence.start_url,
        artifact_dir=evidence.artifact_dir,
        suggested_recipe_yaml=suggested_yaml,
        explanation=str(parsed.get("explanation") or "").strip(),
        confidence=_choice(str(parsed.get("confidence") or "low"), CONFIDENCE_VALUES, "low"),
        assumptions=_list_value(parsed.get("assumptions")),
        warnings=warnings,
        evidence_summary=evidence.evidence_summary,
        selected_strategy=_choice(str(parsed.get("selected_strategy") or ""), STRATEGIES, "not_recommended"),
        referenced_artifact_files=evidence.referenced_artifact_files,
        validation_errors=validation_errors,
        schema_valid=not validation_errors,
    )


def _attempt_is_acceptable(attempt: RecipeRefinementAttempt) -> bool:
    return attempt.schema_valid and attempt.quality_status in {"good", "warning"}


def _candidate_summaries(selector_report: dict) -> list[dict]:
    candidates = selector_report.get("candidates", []) if isinstance(selector_report, dict) else []
    result = []
    for candidate in candidates[:12]:
        if not isinstance(candidate, dict):
            continue
        result.append(
            {
                "selector": candidate.get("selector", ""),
                "kind": candidate.get("kind", ""),
                "text_preview": candidate.get("text_preview", ""),
                "contains_sap_terms": bool(candidate.get("contains_sap_terms", False)),
                "likely_noise": bool(candidate.get("likely_noise", False)),
                "links": candidate.get("links", [])[:5] if isinstance(candidate.get("links"), list) else [],
            }
        )
    return result


def _evidence_summary(url: str, mode: str, candidates: list[dict], visible_text: str) -> str:
    selectors = ", ".join(str(item.get("selector", "")) for item in candidates[:5] if item.get("selector"))
    return (
        f"Capture URL: {url or 'unknown'}; mode: {mode or 'unknown'}; "
        f"candidate selectors: {selectors or 'none'}; visible text chars sampled: {len(visible_text)}."
    )


def _recipe_schema_summary() -> dict:
    return {
        "required": ["source_name", "listing.card_selector", "listing.title_selector", "listing.link_selector"],
        "modes": ["static_html", "rendered_html"],
        "listing_fields": [
            "card_selector",
            "title_selector",
            "link_selector",
            "company_selector",
            "location_selector",
            "remote_selector",
            "rate_selector",
            "workload_selector",
            "posted_date_selector",
            "description_selector",
        ],
        "pattern_fields": [
            "title_regex",
            "job_id_regex",
            "location_regex",
            "remote_regex",
            "rate_regex",
            "workload_regex",
            "posted_date_regex",
            "start_date_regex",
            "language_regex",
            "work_type_regex",
        ],
    }


def _parse_llm_json(text: str) -> dict:
    try:
        return json.loads(_extract_json(text))
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM response was not valid JSON: {exc}") from exc


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM response did not contain a JSON object.")
    return stripped[start : end + 1]


def _read_text(path: Path | None, limit: int) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _list_value(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _choice(value: str, allowed: set[str], default: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in allowed else default
