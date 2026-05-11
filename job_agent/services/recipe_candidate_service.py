from __future__ import annotations

import re
from dataclasses import MISSING, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from job_agent.config import ROOT
from job_agent.services.recipe_suggestion_service import (
    RecipeRefinementAttempt,
    RecipeRefinementResult,
    RecipeSuggestionResult,
)


@dataclass
class RecipeCandidate:
    candidate_id: str
    status: str
    created_at: str
    updated_at: str
    source_name: str
    start_url: str
    artifact_dir: str
    suggested_recipe_yaml: str
    schema_valid: bool
    validation_errors: list[str] = field(default_factory=list)
    selected_strategy: str = ""
    confidence: str = ""
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence_summary: str = ""
    referenced_artifact_files: list[str] = field(default_factory=list)
    refinement_used: bool = False
    refinement_accepted: bool = False
    attempt_count: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)
    quality_status: str = ""
    extracted_job_count: int = 0
    useful_titles: int = 0
    generic_labels: int = 0
    unique_urls: int = 0
    average_description_length: int = 0
    quality_warnings: list[str] = field(default_factory=list)
    rejected_at: str = ""
    rejection_reason: str = ""
    approved_at: str = ""
    approved_recipe_path: str = ""
    approved_source_id: str = ""
    preview_saved: bool = False
    preview_status: str = ""
    preview_extracted_job_count: int = 0
    preview_useful_titles: int = 0
    preview_unique_urls: int = 0
    preview_warnings: list[str] = field(default_factory=list)


@dataclass
class RecipeCandidateSummary:
    candidate_id: str
    status: str
    source_name: str
    created_at: str
    schema_valid: bool
    refinement_accepted: bool
    quality_status: str
    artifact_dir: str


class RecipeCandidateStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.candidates_dir = self.root / "output" / "recipe-candidates"

    def save_candidate_from_suggestion(self, result: RecipeSuggestionResult) -> RecipeCandidate:
        now = _now()
        candidate = RecipeCandidate(
            candidate_id=self._new_candidate_id(result.source_name, result.artifact_dir),
            status="pending",
            created_at=now,
            updated_at=now,
            source_name=result.source_name,
            start_url=result.start_url,
            artifact_dir=str(result.artifact_dir),
            suggested_recipe_yaml=result.suggested_recipe_yaml,
            schema_valid=result.schema_valid,
            validation_errors=list(result.validation_errors),
            selected_strategy=result.selected_strategy,
            confidence=result.confidence,
            assumptions=list(result.assumptions),
            warnings=list(result.warnings),
            evidence_summary=result.evidence_summary,
            referenced_artifact_files=list(result.referenced_artifact_files),
        )
        self._write(candidate)
        return candidate

    def save_candidate_from_refinement(self, result: RecipeRefinementResult) -> RecipeCandidate:
        candidate = self.save_candidate_from_suggestion(result.final_result)
        last_attempt = result.attempts[-1] if result.attempts else None
        candidate.refinement_used = True
        candidate.refinement_accepted = result.accepted
        candidate.attempt_count = len(result.attempts)
        candidate.attempts = [_attempt_to_dict(attempt) for attempt in result.attempts]
        if last_attempt:
            candidate.quality_status = last_attempt.quality_status
            candidate.extracted_job_count = last_attempt.extracted_job_count
            candidate.useful_titles = last_attempt.useful_titles
            candidate.generic_labels = last_attempt.generic_labels
            candidate.unique_urls = last_attempt.unique_urls
            candidate.average_description_length = last_attempt.average_description_length
            candidate.quality_warnings = list(last_attempt.quality_warnings)
        self._write(candidate)
        return candidate

    def list_candidates(self, status: str = "") -> list[RecipeCandidateSummary]:
        candidates = []
        for path in sorted(self.candidates_dir.glob("*.yaml")):
            try:
                candidate = self.load_candidate(path.stem)
            except ValueError:
                continue
            if status and candidate.status != status:
                continue
            candidates.append(
                RecipeCandidateSummary(
                    candidate_id=candidate.candidate_id,
                    status=candidate.status,
                    source_name=candidate.source_name,
                    created_at=candidate.created_at,
                    schema_valid=candidate.schema_valid,
                    refinement_accepted=candidate.refinement_accepted,
                    quality_status=candidate.quality_status,
                    artifact_dir=candidate.artifact_dir,
                )
            )
        return candidates

    def load_candidate(self, candidate_id: str) -> RecipeCandidate:
        path = self._candidate_path(candidate_id)
        if not path.exists():
            raise ValueError(f"Recipe candidate not found: {candidate_id}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Recipe candidate file is invalid: {candidate_id}")
        return RecipeCandidate(**_candidate_fields(data))

    def reject_candidate(self, candidate_id: str, reason: str = "") -> RecipeCandidate:
        candidate = self.load_candidate(candidate_id)
        now = _now()
        candidate.status = "rejected"
        candidate.updated_at = now
        candidate.rejected_at = now
        candidate.rejection_reason = reason.strip()
        self._write(candidate)
        return candidate

    def approve_candidate(
        self,
        candidate_id: str,
        *,
        recipe_path: str,
        source_id: str = "",
        preview_saved: bool = False,
        preview_status: str = "",
        preview_extracted_job_count: int = 0,
        preview_useful_titles: int = 0,
        preview_unique_urls: int = 0,
        preview_warnings: list[str] | None = None,
    ) -> RecipeCandidate:
        candidate = self.load_candidate(candidate_id)
        now = _now()
        candidate.status = "approved"
        candidate.updated_at = now
        candidate.approved_at = now
        candidate.approved_recipe_path = recipe_path
        candidate.approved_source_id = source_id.strip()
        candidate.preview_saved = preview_saved
        candidate.preview_status = preview_status
        candidate.preview_extracted_job_count = preview_extracted_job_count
        candidate.preview_useful_titles = preview_useful_titles
        candidate.preview_unique_urls = preview_unique_urls
        candidate.preview_warnings = list(preview_warnings or [])
        self._write(candidate)
        return candidate

    def candidate_path(self, candidate_id: str) -> Path:
        return self._candidate_path(candidate_id)

    def _write(self, candidate: RecipeCandidate) -> None:
        self.candidates_dir.mkdir(parents=True, exist_ok=True)
        path = self._candidate_path(candidate.candidate_id)
        path.write_text(yaml.safe_dump(_candidate_to_dict(candidate), sort_keys=False), encoding="utf-8")

    def _candidate_path(self, candidate_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", candidate_id):
            raise ValueError(f"Invalid recipe candidate id: {candidate_id}")
        return self.candidates_dir / f"{candidate_id}.yaml"

    def _new_candidate_id(self, source_name: str, artifact_dir: Path) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        base_slug = _slug(source_name) or _slug(Path(artifact_dir).name) or "recipe-candidate"
        candidate_id = f"{stamp}-{base_slug}"
        if not self._candidate_path(candidate_id).exists():
            return candidate_id
        suffix = 2
        while self._candidate_path(f"{candidate_id}-{suffix}").exists():
            suffix += 1
        return f"{candidate_id}-{suffix}"


def _candidate_to_dict(candidate: RecipeCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
        "source_name": candidate.source_name,
        "start_url": candidate.start_url,
        "artifact_dir": candidate.artifact_dir,
        "suggested_recipe_yaml": candidate.suggested_recipe_yaml,
        "schema_valid": candidate.schema_valid,
        "validation_errors": candidate.validation_errors,
        "selected_strategy": candidate.selected_strategy,
        "confidence": candidate.confidence,
        "assumptions": candidate.assumptions,
        "warnings": candidate.warnings,
        "evidence_summary": candidate.evidence_summary,
        "referenced_artifact_files": candidate.referenced_artifact_files,
        "refinement_used": candidate.refinement_used,
        "refinement_accepted": candidate.refinement_accepted,
        "attempt_count": candidate.attempt_count,
        "attempts": candidate.attempts,
        "quality_status": candidate.quality_status,
        "extracted_job_count": candidate.extracted_job_count,
        "useful_titles": candidate.useful_titles,
        "generic_labels": candidate.generic_labels,
        "unique_urls": candidate.unique_urls,
        "average_description_length": candidate.average_description_length,
        "quality_warnings": candidate.quality_warnings,
        "rejected_at": candidate.rejected_at,
        "rejection_reason": candidate.rejection_reason,
        "approved_at": candidate.approved_at,
        "approved_recipe_path": candidate.approved_recipe_path,
        "approved_source_id": candidate.approved_source_id,
        "preview_saved": candidate.preview_saved,
        "preview_status": candidate.preview_status,
        "preview_extracted_job_count": candidate.preview_extracted_job_count,
        "preview_useful_titles": candidate.preview_useful_titles,
        "preview_unique_urls": candidate.preview_unique_urls,
        "preview_warnings": candidate.preview_warnings,
    }


def _candidate_fields(data: dict[str, Any]) -> dict[str, Any]:
    fields = RecipeCandidate.__dataclass_fields__
    values = {}
    for key, field_info in fields.items():
        if key in data:
            values[key] = data[key]
        elif field_info.default is not MISSING:
            values[key] = field_info.default
        elif field_info.default_factory is not MISSING:
            values[key] = field_info.default_factory()
    values.setdefault("validation_errors", [])
    values.setdefault("assumptions", [])
    values.setdefault("warnings", [])
    values.setdefault("referenced_artifact_files", [])
    values.setdefault("attempts", [])
    values.setdefault("quality_warnings", [])
    values.setdefault("preview_warnings", [])
    return values


def _attempt_to_dict(attempt: RecipeRefinementAttempt) -> dict[str, Any]:
    return {
        "attempt_number": attempt.attempt_number,
        "suggested_recipe_yaml": attempt.suggested_recipe_yaml,
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


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60].strip("-")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
