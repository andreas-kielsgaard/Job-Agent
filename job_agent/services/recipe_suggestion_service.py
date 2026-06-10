from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml
from bs4 import BeautifulSoup

from job_agent.config import ROOT
from job_agent.llm import LlmService
from job_agent.services.extraction_quality import job_url_quality
from job_agent.services.job_board_recipe_service import (
    check_recipe_against_html,
    extract_job_detail_from_html,
    extract_jobs_with_recipe_from_api_payload,
    quality_from_recipe_result,
)
from job_agent.services.recipe_calibration_service import classify_recipe_field_label, label_unsupported_reason
from job_agent.services.recipes.mapping import _selectors, job_board_recipe_from_mapping
from job_agent.services.recipes.source_test_insight import (
    apply_source_test_insight_to_recipe as _apply_source_test_insight_to_recipe,
)
from job_agent.services.recipes.source_test_insight import (
    source_test_insight_from_payload as _source_test_insight_from_payload,
)
from job_agent.services.recipes.source_test_insight import (
    source_test_recipe_warnings as _source_test_recipe_warnings,
)
from job_agent.services.recipes.source_test_insight import (
    suggestion_conflicts_with_source_test_insight as _suggestion_conflicts_with_source_test_insight,
)

EXPECTED_ARTIFACT_FILES = [
    "summary.md",
    "selector-report.json",
    "candidate-elements.html",
    "visible-text.txt",
    "page.html",
]
CONFIDENCE_VALUES = {"low", "medium", "high"}
STRATEGIES = {"selector_based", "pattern_based", "selector_and_pattern", "api_based", "not_recommended"}


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
    source_test_insight: dict[str, Any] = field(default_factory=dict)


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
    source_test_insight: dict[str, Any] | None = None,
    llm_client: RecipeSuggestionLlmClient | None = None,
    root: Path = ROOT,
) -> RecipeSuggestionResult:
    evidence = load_recipe_suggestion_evidence(
        artifact_dir,
        source_name=source_name,
        start_url=start_url,
        existing_recipe_path=existing_recipe_path,
        source_test_insight=source_test_insight,
    )
    if no_evidence := _no_recipe_evidence_result(evidence):
        return no_evidence
    deterministic = _deterministic_suggestion_result(evidence)
    client = llm_client or LlmServiceRecipeSuggestionClient(root)
    prompt = build_recipe_suggestion_prompt(evidence)
    try:
        raw_response = client.suggest(prompt)
    except RuntimeError as exc:
        if (
            deterministic
            and deterministic.schema_valid
            and not _suggestion_conflicts_with_source_test_insight(deterministic)
        ):
            deterministic.warnings.append(f"LLM refinement unavailable; saved deterministic draft instead: {exc}")
            return deterministic
        raise
    return _suggestion_result_from_response(evidence, raw_response)


def suggest_recipe_with_refinement(
    artifact_dir: Path,
    source_name: str = "",
    start_url: str = "",
    existing_recipe_path: Path | None = None,
    source_test_insight: dict[str, Any] | None = None,
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
        source_test_insight=source_test_insight,
    )
    if no_evidence := _no_recipe_evidence_result(evidence):
        attempt = RecipeRefinementAttempt(
            attempt_number=1,
            suggested_recipe_yaml="",
            schema_valid=False,
            validation_errors=list(no_evidence.validation_errors),
            quality_status="poor",
            quality_warnings=list(no_evidence.warnings),
            revision_reason="Calibration evidence did not contain a repeated job listing or detail-page sample.",
        )
        return RecipeRefinementResult(final_result=no_evidence, attempts=[attempt], accepted=False)
    client = llm_client or LlmServiceRecipeSuggestionClient(root)
    attempts: list[RecipeRefinementAttempt] = []
    final_result: RecipeSuggestionResult | None = None

    deterministic = _deterministic_suggestion_result(evidence)
    if deterministic and deterministic.schema_valid:
        final_result = deterministic
        attempt = evaluate_suggestion_against_artifact(deterministic)
        attempt.attempt_number = 1
        attempts.append(attempt)
        if _attempt_is_acceptable(attempt):
            return RecipeRefinementResult(final_result=deterministic, attempts=attempts, accepted=True)

    prompt = build_recipe_suggestion_prompt(evidence)
    for attempt_number in range(len(attempts) + 1, max_attempts + 1):
        try:
            raw_response = client.suggest(prompt)
        except RuntimeError:
            if final_result is not None:
                return RecipeRefinementResult(final_result=final_result, attempts=attempts, accepted=False)
            raise
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

    try:
        data = yaml.safe_load(result.suggested_recipe_yaml) or {}
        recipe = job_board_recipe_from_mapping(data, label="suggested_recipe_yaml")
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

    page_path = result.artifact_dir / "page.html"
    api_fixture_path = _api_fixture_path(result.artifact_dir)
    if recipe.listing_api.url and api_fixture_path:
        try:
            payload = json.loads(api_fixture_path.read_text(encoding="utf-8", errors="replace"))
            extraction = extract_jobs_with_recipe_from_api_payload(
                payload,
                base_url=result.start_url or recipe.start_url,
                recipe=recipe,
            )
            quality = quality_from_recipe_result(extraction, recipe)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return RecipeRefinementAttempt(
                attempt_number=0,
                suggested_recipe_yaml=result.suggested_recipe_yaml,
                schema_valid=True,
                validation_errors=[],
                quality_status="poor",
                quality_warnings=[f"Local API recipe quality check failed: {exc}"],
                revision_reason="Local API extraction check failed.",
            )
    else:
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
            html = page_path.read_text(encoding="utf-8", errors="replace")
            quality = check_recipe_against_html(html, result.start_url or recipe.start_url, recipe, follow_detail=False)
        except (OSError, ValueError) as exc:
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
    detail_path = result.artifact_dir / "detail-sample.html"
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
    elif _all_extracted_urls_are_non_jobs(quality):
        quality_status = "poor"
        warnings.append("Extracted non-job URLs pointing to files, assets, legal pages, or other non-job pages.")
        revision_reason = "Recipe extracted non-job URLs."
    elif quality.generic_title_count >= quality.candidate_count:
        quality_status = "poor"
        warnings.append("All extracted titles look generic.")
        revision_reason = "Recipe extracted only generic titles."
    elif quality.average_description_length < 40 and not detail_path.exists():
        quality_status = "warning"
        warnings.append(
            "Average description length is low; verify the API field mapping or card selector captures enough text."
        )

    semantic_warnings = _semantic_recipe_warnings(recipe, result.artifact_dir)
    if semantic_warnings:
        quality_status = "poor"
        warnings.extend(semantic_warnings)
        revision_reason = revision_reason or "Recipe selectors contradict visible page labels."

    source_test_warnings = _source_test_recipe_warnings(recipe, result.source_test_insight)
    if source_test_warnings:
        quality_status = "poor"
        warnings.extend(source_test_warnings)
        revision_reason = revision_reason or "Recipe keeps a pagination strategy that the source test already rejected."

    if detail_path.exists():
        detail_report = _read_json(result.artifact_dir / "selector-report.json")
        detail_url = str(detail_report.get("detail_sample_url") or result.start_url or recipe.start_url)
        if not recipe.detail.follow:
            quality_status = "poor"
            warnings.append("A detail-page sample is available, but the recipe does not follow job detail pages.")
            revision_reason = revision_reason or "Recipe omitted detail-page navigation."
        else:
            detail_html = detail_path.read_text(encoding="utf-8", errors="replace")
            detail_job = extract_job_detail_from_html(detail_html, detail_url, recipe)
            found_detail_fields = _present_detail_fields(detail_job)
            if not found_detail_fields:
                quality_status = "poor"
                warnings.append("Detail-page sample did not produce reportable fields.")
                revision_reason = revision_reason or "Recipe detail selectors did not extract useful detail fields."
            elif len(detail_job.description.strip()) < 120:
                quality_status = "warning" if quality_status == "good" else quality_status
                warnings.append("Detail-page sample produced a short description; verify detail selectors.")

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
    source_test_insight: dict[str, Any] | None = None,
) -> RecipeSuggestionEvidence:
    artifact_dir = Path(artifact_dir)
    warnings: list[str] = []
    referenced: list[str] = []
    for name in EXPECTED_ARTIFACT_FILES:
        if (artifact_dir / name).exists():
            referenced.append(name)
        else:
            warnings.append(f"Missing artifact file: {name}")
    api_fixture = _api_fixture_path(artifact_dir)
    if api_fixture:
        referenced.append(str(api_fixture.relative_to(artifact_dir).as_posix()))

    summary = _read_text(artifact_dir / "summary.md", 3500)
    selector_report = _read_json(artifact_dir / "selector-report.json")
    visible_text = _read_text(artifact_dir / "visible-text.txt", 2200)
    candidate_html = _read_text(artifact_dir / "candidate-elements.html", 1200)
    detail_visible_text = _read_text(artifact_dir / "detail-visible-text.txt", 2200)
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
        "observed_pagination_links": selector_report.get("observed_pagination_links", [])[:10]
        if isinstance(selector_report, dict)
        else [],
        "observed_ajax_pagination_templates": selector_report.get("observed_ajax_pagination_templates", [])[:10]
        if isinstance(selector_report, dict)
        else [],
        "observed_api_candidates": selector_report.get("observed_api_candidates", [])[:5]
        if isinstance(selector_report, dict)
        else [],
        "observed_interactive_pagination_controls": selector_report.get(
            "observed_interactive_pagination_controls", []
        )[:10]
        if isinstance(selector_report, dict)
        else [],
        "observed_application_entries": selector_report.get("observed_application_entries", [])[:10]
        if isinstance(selector_report, dict)
        else [],
        "source_session_used": bool(selector_report.get("source_session_used"))
        if isinstance(selector_report, dict)
        else False,
        "source_session_scope": str(selector_report.get("source_session_scope") or "")
        if isinstance(selector_report, dict)
        else "",
        "detail_sample_url": str(selector_report.get("detail_sample_url") or "")
        if isinstance(selector_report, dict)
        else "",
        "detail_sample_captured": bool(selector_report.get("detail_sample_captured"))
        if isinstance(selector_report, dict)
        else False,
        "detail_visible_text_sample": detail_visible_text,
        "recipe_blueprint": selector_report.get("recipe_blueprint", {}) if isinstance(selector_report, dict) else {},
        "field_observations": (selector_report.get("recipe_blueprint", {}) or {}).get("field_observations", {})
        if isinstance(selector_report, dict)
        else {},
        "visible_text_sample": visible_text,
        "candidate_elements_sample": candidate_html,
        "summary": summary,
        "existing_recipe_yaml": existing_recipe,
        "source_test_insight": source_test_insight or {},
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
        "The YAML may use only this schema: source_name, start_url, mode, access, listing, pagination, accept, "
        "listing_api, detail_api, reject, patterns, limits, detail. Use listing_api/detail_api only when the "
        "local evidence includes observed_api_candidates or a deterministic recipe_blueprint with API access. "
        "Do not include Python code, browser scripts, arbitrary adapters, credentials, cookie values, hidden "
        "endpoint assumptions, or new network/API discovery. API recipes must use only the exact public "
        "page-declared request shape captured in evidence and must not add auth or cookies. If local evidence "
        "shows that pagination or listings require signing in, set access.requires_session true with a short "
        "setup_hint.\n"
        "A deterministic recipe_blueprint may be included. Prefer preserving selectors from that blueprint when "
        "the local evidence supports them; revise only when the evidence contradicts it.\n"
        "If source_test_insight is present, it is the latest live source-test diagnosis. Address it directly; "
        "preserve the tested strategy when pagination_working_with_unique_pages is true, do not preserve URL "
        "pagination when pagination_duplicate_postings is true or a failed capability proves duplicate pages, "
        "and use browser_click only when the live test says interactive pagination controls require browser-click "
        "pagination. Set access.requires_session only for explicit source-access evidence such as a login gate, "
        "source_access_failed, source_access_requires_session, or a connected source session that made pagination "
        "work; do not infer a session requirement from speculative warning text.\n"
        "Use table headers and detail label/value observations to map fields semantically. Do not map `Category` "
        "to workload, `Application deadline` or `Closing date` to posted_date, or `End date` to start_date. "
        "If the schema lacks a matching report field, omit that selector and mention the unsupported field.\n"
        "Set pagination.strategy to url for normal href page links, ajax only when the local evidence exposes a "
        "page URL template that can be fetched directly, and browser_click when visible next/page controls require "
        "clicking rather than href navigation. For listing_api, use listing_api.pagination rather than HTML "
        "pagination. Include pagination selectors, templates, or API pagination fields that match that strategy. "
        "Use detail.follow only when a detail sample URL or detail sample text justifies job-detail enrichment.\n"
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
        "The YAML may use only this schema: source_name, start_url, mode, access, listing, pagination, accept, "
        "listing_api, detail_api, reject, patterns, limits, detail. Use listing_api/detail_api only from captured "
        "observed_api_candidates or an API recipe_blueprint. Do not include Python code, browser scripts, arbitrary "
        "adapters, credentials, cookie values, hidden endpoint assumptions, or new network/API discovery. API "
        "requests must preserve the exact public page-declared request shape captured in evidence.\n"
        "If source_test_insight is present, fix live failures first. Preserve the tested strategy when "
        "pagination_working_with_unique_pages is true; do not keep a pagination strategy that failed with "
        "pagination_duplicate_postings or inaccessible pages. If the live test says interactive controls require "
        "browser-click pagination, switch to browser_click. Set access.requires_session only from explicit "
        "source-access evidence, not from speculative warning text.\n"
        "Use visible labels as ground truth: Category is not workload, Application deadline/Closing date is not "
        "posted_date, and End date is not start_date. Omit unsupported fields instead of forcing them into a "
        "nearby report field.\n"
        "Set pagination.strategy to url, ajax, or browser_click according to the evidence, and include the matching "
        "selectors or AJAX URL template. Visible controls without usable hrefs should use browser_click.\n"
        "Do not assume access to any page beyond the saved local artifact.\n\n"
        f"Evidence JSON:\n{json.dumps(evidence.prompt_payload, ensure_ascii=False, indent=2)}\n\n"
        f"Previous suggested YAML:\n{attempt.suggested_recipe_yaml}\n\n"
        f"Validation and local extraction report:\n{json.dumps(report, ensure_ascii=False, indent=2)}"
    )


def _deterministic_suggestion_result(evidence: RecipeSuggestionEvidence) -> RecipeSuggestionResult | None:
    blueprint = evidence.prompt_payload.get("recipe_blueprint")
    if not isinstance(blueprint, dict):
        return None
    recipe_data = blueprint.get("recipe")
    if not isinstance(recipe_data, dict) or not recipe_data:
        return None
    recipe_data = deepcopy(recipe_data)
    recipe_data["source_name"] = evidence.source_name
    recipe_data["start_url"] = evidence.start_url or str(recipe_data.get("start_url") or "")
    _normalize_recipe_capabilities(recipe_data)
    insight_warnings = _apply_source_test_insight_to_recipe(recipe_data, evidence.prompt_payload)
    _normalize_recipe_capabilities(recipe_data)
    recipe_yaml = yaml.safe_dump(recipe_data, sort_keys=False, allow_unicode=True).strip()
    validation_errors = validate_suggested_recipe_yaml(recipe_yaml)
    warnings = list(evidence.warnings) + _list_value(blueprint.get("warnings")) + insight_warnings
    if validation_errors:
        warnings.extend(validation_errors)
    listing = recipe_data.get("listing") or {}
    listing_api = recipe_data.get("listing_api") or {}
    detail = recipe_data.get("detail") or {}
    pagination = recipe_data.get("pagination") or {}
    explanation_parts = [
        (
            f"Deterministic draft selected API records from `{listing_api.get('url', '')}`."
            if listing_api
            else f"Deterministic draft selected listing cards with `{listing.get('card_selector', '')}`."
        ),
    ]
    if detail.get("follow"):
        explanation_parts.append(
            "It includes one-detail-page enrichment because the calibration artifact captured a detail sample."
        )
    if pagination.get("page_link_selector") or pagination.get("next_selector"):
        explanation_parts.append("It includes pagination selectors observed in the listing page.")
    if insight_warnings:
        explanation_parts.append("It applies the latest source-test pagination diagnosis.")
    return RecipeSuggestionResult(
        source_name=evidence.source_name,
        start_url=evidence.start_url,
        artifact_dir=evidence.artifact_dir,
        suggested_recipe_yaml=recipe_yaml,
        explanation=" ".join(explanation_parts),
        confidence=_choice(str(blueprint.get("confidence") or "medium"), CONFIDENCE_VALUES, "medium"),
        assumptions=[
            "Generated from deterministic selector evidence before optional LLM refinement.",
            "Review against a live compatibility check before enabling daily runs.",
        ],
        warnings=warnings,
        evidence_summary=evidence.evidence_summary,
        selected_strategy="api_based" if listing_api else "selector_based",
        referenced_artifact_files=evidence.referenced_artifact_files,
        validation_errors=validation_errors,
        schema_valid=not validation_errors,
        source_test_insight=_source_test_insight_from_payload(evidence.prompt_payload),
    )


def _all_extracted_urls_are_non_jobs(quality) -> bool:
    candidates = list(getattr(quality, "candidates", []) or [])
    if not candidates:
        return False
    return all(job_url_quality(str(getattr(candidate, "url", "") or "")) == "non_job" for candidate in candidates)


def _no_recipe_evidence_result(evidence: RecipeSuggestionEvidence) -> RecipeSuggestionResult | None:
    blueprint = evidence.prompt_payload.get("recipe_blueprint")
    if not isinstance(blueprint, dict):
        return None
    if blueprint.get("status") != "not_recommended":
        return None
    warnings = list(evidence.warnings) + _list_value(blueprint.get("warnings"))
    warnings.append(
        "The captured page did not expose repeated job cards or a job-detail link. "
        "Try a browser-rendered capture if the job list is loaded by JavaScript."
    )
    return RecipeSuggestionResult(
        source_name=evidence.source_name,
        start_url=evidence.start_url,
        artifact_dir=evidence.artifact_dir,
        suggested_recipe_yaml="",
        explanation=(
            "The saved calibration artifact did not contain enough job-list evidence to generate a reading plan."
        ),
        confidence="low",
        assumptions=[],
        warnings=warnings,
        evidence_summary=evidence.evidence_summary,
        selected_strategy="not_recommended",
        referenced_artifact_files=evidence.referenced_artifact_files,
        validation_errors=["No stable repeated listing card selector was found."],
        schema_valid=False,
        source_test_insight=_source_test_insight_from_payload(evidence.prompt_payload),
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
    suggested_yaml, insight_warnings = _normalize_suggested_recipe_yaml(
        str(parsed.get("suggested_recipe_yaml") or "").strip(),
        prompt_payload=evidence.prompt_payload,
    )
    validation_errors = validate_suggested_recipe_yaml(suggested_yaml)
    warnings = list(evidence.warnings) + _list_value(parsed.get("warnings")) + insight_warnings
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
        source_test_insight=_source_test_insight_from_payload(evidence.prompt_payload),
    )


def _normalize_suggested_recipe_yaml(
    value: str,
    *,
    prompt_payload: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    if not value.strip():
        return value, []
    try:
        data = yaml.safe_load(value)
    except yaml.YAMLError:
        return value, []
    if not isinstance(data, dict):
        return value, []
    _normalize_recipe_capabilities(data)
    insight_warnings = _apply_source_test_insight_to_recipe(data, prompt_payload or {})
    _normalize_recipe_capabilities(data)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip(), insight_warnings


def _normalize_recipe_capabilities(recipe_data: dict) -> None:
    pagination = recipe_data.get("pagination")
    if isinstance(pagination, dict) and not str(pagination.get("strategy") or "").strip():
        if str(pagination.get("ajax_url_template") or "").strip():
            pagination["strategy"] = "ajax"
        elif str(pagination.get("click_selector") or "").strip():
            pagination["strategy"] = "browser_click"
        elif (
            str(pagination.get("page_link_selector") or "").strip()
            or str(pagination.get("next_selector") or "").strip()
        ):
            pagination["strategy"] = "url"
    access = recipe_data.get("access")
    if (
        isinstance(access, dict)
        and "requires_session" not in access
        and str(access.get("session_scope") or access.get("setup_hint") or "").strip()
    ):
        access["requires_session"] = True


def _attempt_is_acceptable(attempt: RecipeRefinementAttempt) -> bool:
    return attempt.schema_valid and attempt.quality_status in {"good", "warning"}


def _semantic_recipe_warnings(recipe, artifact_dir: Path) -> list[str]:
    warnings: list[str] = []
    page_path = artifact_dir / "page.html"
    if page_path.exists():
        page_html = page_path.read_text(encoding="utf-8", errors="replace")
        warnings.extend(_listing_label_warnings(recipe, page_html))
    detail_path = artifact_dir / "detail-sample.html"
    if detail_path.exists():
        detail_html = detail_path.read_text(encoding="utf-8", errors="replace")
        warnings.extend(_detail_label_warnings(recipe, detail_html))
    return warnings


def _listing_label_warnings(recipe, html: str) -> list[str]:
    if recipe.listing_api.url or not recipe.listing.card_selector:
        return []
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(recipe.listing.card_selector)
    if not cards or cards[0].name != "tr":
        return []
    headers = _table_headers_for_row(cards[0])
    if not headers:
        return []
    warnings: list[str] = []
    selector_fields = {
        "location": recipe.listing.location_selector,
        "remote": recipe.listing.remote_selector,
        "rate": recipe.listing.rate_selector,
        "workload": recipe.listing.workload_selector,
        "posted_date": recipe.listing.posted_date_selector,
        "start_date": recipe.listing.start_date_selector,
    }
    for field_name, selector_value in selector_fields.items():
        for selector in _selectors(selector_value):
            column = _td_column_index(selector)
            if column is None or column < 1 or column > len(headers):
                continue
            label = headers[column - 1]
            expected_key = classify_recipe_field_label(label)
            expected_field = _field_name_from_selector_key(expected_key)
            unsupported_reason = label_unsupported_reason(label)
            if unsupported_reason:
                warnings.append(
                    f"listing.{field_name}_selector points at column {column} labelled `{label}`. {unsupported_reason}"
                )
            elif expected_field and expected_field != field_name:
                warnings.append(
                    f"listing.{field_name}_selector points at column {column} labelled `{label}`, "
                    f"which matches `{expected_field}`."
                )
    return warnings


def _detail_label_warnings(recipe, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    warnings: list[str] = []
    selector_fields = {
        "location": recipe.detail.location_selector,
        "remote": recipe.detail.remote_selector,
        "rate": recipe.detail.rate_selector,
        "workload": recipe.detail.workload_selector,
        "posted_date": recipe.detail.posted_date_selector,
        "start_date": recipe.detail.start_date_selector,
    }
    for field_name, selector_value in selector_fields.items():
        for selector in _selectors(selector_value):
            match = soup.select_one(selector)
            if not match:
                continue
            label = _leading_label(match.get_text(" ", strip=True))
            if not label:
                continue
            expected_key = classify_recipe_field_label(label)
            expected_field = _field_name_from_selector_key(expected_key)
            unsupported_reason = label_unsupported_reason(label)
            if unsupported_reason:
                warnings.append(f"detail.{field_name}_selector reads `{label}`. {unsupported_reason}")
            elif expected_field and expected_field != field_name:
                warnings.append(f"detail.{field_name}_selector reads `{label}`, which matches `{expected_field}`.")
    return warnings


def _table_headers_for_row(row) -> list[str]:
    cells = row.find_all("td", recursive=False)
    table = row.find_parent("table")
    if not cells or not table:
        return []
    header_rows = table.select("thead tr")
    if not header_rows:
        header_rows = [candidate for candidate in table.find_all("tr") if candidate.find("th")]
    headers: list[str] = []
    for header_row in header_rows:
        candidate_headers = [cell.get_text(" ", strip=True) for cell in header_row.find_all("th", recursive=False)]
        if len(candidate_headers) >= len(headers):
            headers = candidate_headers
    if len(headers) < len(cells):
        headers.extend([""] * (len(cells) - len(headers)))
    return headers[: len(cells)]


def _td_column_index(selector: str) -> int | None:
    match = re.search(r"td:nth-of-type\((\d+)\)", selector)
    return int(match.group(1)) if match else None


def _field_name_from_selector_key(selector_key: str) -> str:
    return selector_key.removesuffix("_selector") if selector_key else ""


def _leading_label(text: str) -> str:
    if ":" not in text:
        return ""
    label = text.split(":", 1)[0].strip()
    return label if 0 < len(label) <= 40 else ""


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


def _present_detail_fields(job) -> list[str]:
    fields = []
    for field_name in ["title", "location", "remote", "rate", "workload", "posted_date", "start_date", "description"]:
        value = getattr(job, field_name)
        if field_name == "description":
            if len(str(value).strip()) >= 120:
                fields.append(field_name)
        elif str(value).strip() and str(value).strip() != "Not listed":
            fields.append(field_name)
    if getattr(job, "languages", []):
        fields.append("languages")
    return fields


def _evidence_summary(url: str, mode: str, candidates: list[dict], visible_text: str) -> str:
    selectors = ", ".join(str(item.get("selector", "")) for item in candidates[:5] if item.get("selector"))
    return (
        f"Capture URL: {url or 'unknown'}; mode: {mode or 'unknown'}; "
        f"candidate selectors: {selectors or 'none'}; visible text chars sampled: {len(visible_text)}."
    )


def _recipe_schema_summary() -> dict:
    return {
        "required": [
            "source_name",
            "either listing selectors (listing.card_selector, listing.title_selector, listing.link_selector) "
            "or listing_api with url, results_path, fields.title, and fields.url/url_template",
        ],
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
            "start_date_selector",
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
        "pagination_fields": [
            "strategy",
            "page_link_selector",
            "next_selector",
            "click_selector",
            "ajax_url_template",
            "max_pages",
            "request_delay_seconds",
        ],
        "listing_api_fields": {
            "request": ["method", "url", "headers", "params", "body", "results_path", "total_path"],
            "fields": [
                "title",
                "url",
                "url_template",
                "application_url",
                "company",
                "recruiter",
                "end_client",
                "location",
                "remote",
                "rate",
                "workload",
                "contract_duration",
                "posted_date",
                "start_date",
                "deadline",
                "languages",
                "description",
                "description_html",
                "raw_text",
                "job_id",
            ],
            "pagination": [
                "strategy",
                "page_param",
                "page_start",
                "offset_param",
                "offset_start",
                "page_size_param",
                "page_size",
                "max_pages",
                "request_delay_seconds",
            ],
        },
        "detail_api_fields": "same request and field mapping shape as listing_api; results_path is optional for detail.",
        "access_fields": ["requires_session", "session_scope", "setup_hint"],
        "detail_fields": [
            "follow",
            "use_json_ld",
            "title_selector",
            "description_selector",
            "location_selector",
            "remote_selector",
            "rate_selector",
            "workload_selector",
            "posted_date_selector",
            "start_date_selector",
            "language_selector",
            "request_delay_seconds",
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


def _api_fixture_path(artifact_dir: Path) -> Path | None:
    candidates = sorted(artifact_dir.glob("api-listing-response-*.json"))
    if not candidates:
        candidates = sorted(artifact_dir.glob("**/api-listing-response-*.json"))
    return candidates[0] if candidates else None


def _list_value(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _choice(value: str, allowed: set[str], default: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in allowed else default
