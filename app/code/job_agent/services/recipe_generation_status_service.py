from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from job_agent.config import ROOT
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.recipe_artifact_service import RecipeArtifactService, RecipeArtifactSummary
from job_agent.services.recipe_candidate_policy import candidate_is_reviewable
from job_agent.services.recipe_candidate_service import RecipeCandidate, RecipeCandidateStore
from job_agent.services.source_registry_service import SourceRegistryEntry, SourceRegistryService


@dataclass
class RecipeGenerationStatus:
    source_id: str
    source_name: str = ""
    source_url: str = ""
    source_recipe_path: str = ""
    artifact_count: int = 0
    best_artifact: RecipeArtifactSummary | None = None
    pending_candidates: int = 0
    rejected_candidates: int = 0
    approved_candidates: int = 0
    latest_candidate_id: str = ""
    latest_candidate_status: str = ""
    reviewable_pending_candidates: int = 0
    latest_reviewable_candidate_id: str = ""
    latest_reviewable_candidate_status: str = ""
    latest_approved_candidate_id: str = ""
    latest_approved_recipe_path: str = ""
    approved_matches_source_recipe_path: bool = False
    source_health_status: str = "untested"
    source_health_summary: str = ""
    execution_entry_exists: bool = False
    execution_enabled: bool = False
    warnings: list[str] = field(default_factory=list)


class RecipeGenerationStatusService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.registry = SourceRegistryService(self.root)
        self.artifacts = RecipeArtifactService(self.root)
        self.candidates = RecipeCandidateStore(self.root)
        self.execution = ExecutionSourceService(self.root)

    def build_for_source(self, source_id: str) -> RecipeGenerationStatus:
        source = self.registry.get_source(source_id)
        if not source:
            raise ValueError(f"Source not found: {source_id}")
        artifacts = self.artifacts.list_artifacts_for_source(source)
        candidates = self._matching_candidates(source, artifacts)
        approved = [candidate for candidate in candidates if candidate.status == "approved"]
        reviewable_pending = [candidate for candidate in candidates if candidate_is_reviewable(candidate)]
        latest = candidates[0] if candidates else None
        latest_reviewable = reviewable_pending[0] if reviewable_pending else None
        latest_approved = approved[0] if approved else None
        execution_entry = self.execution.find_by_source_id(source.id)
        status = RecipeGenerationStatus(
            source_id=source.id,
            source_name=source.name,
            source_url=source.url,
            source_recipe_path=source.recipe_path,
            artifact_count=len(artifacts),
            best_artifact=artifacts[0] if artifacts else None,
            pending_candidates=sum(1 for candidate in candidates if candidate.status == "pending"),
            rejected_candidates=sum(1 for candidate in candidates if candidate.status == "rejected"),
            approved_candidates=len(approved),
            latest_candidate_id=latest.candidate_id if latest else "",
            latest_candidate_status=latest.status if latest else "",
            reviewable_pending_candidates=len(reviewable_pending),
            latest_reviewable_candidate_id=latest_reviewable.candidate_id if latest_reviewable else "",
            latest_reviewable_candidate_status=latest_reviewable.status if latest_reviewable else "",
            latest_approved_candidate_id=latest_approved.candidate_id if latest_approved else "",
            latest_approved_recipe_path=latest_approved.approved_recipe_path if latest_approved else "",
            approved_matches_source_recipe_path=bool(
                latest_approved
                and source.recipe_path
                and _normalize_path(latest_approved.approved_recipe_path) == _normalize_path(source.recipe_path)
            ),
            source_health_status=source.health.health_status,
            source_health_summary=source.health.health_summary,
            execution_entry_exists=bool(execution_entry),
            execution_enabled=bool(execution_entry and execution_entry.get("enabled", True)),
        )
        status.warnings = _warnings(status, source, latest)
        return status

    def _matching_candidates(
        self,
        source: SourceRegistryEntry,
        artifacts: list[RecipeArtifactSummary],
    ) -> list[RecipeCandidate]:
        artifact_dirs = {artifact.artifact_dir for artifact in artifacts}
        result = []
        for summary in self.candidates.list_candidates():
            try:
                candidate = self.candidates.load_candidate(summary.candidate_id)
            except ValueError:
                continue
            if _candidate_matches_source(candidate, source, artifact_dirs):
                result.append(candidate)
        return sorted(result, key=lambda item: item.created_at, reverse=True)


def _warnings(status: RecipeGenerationStatus, source: SourceRegistryEntry, latest: RecipeCandidate | None) -> list[str]:
    warnings = []
    if status.latest_approved_recipe_path and source.recipe_path and not status.approved_matches_source_recipe_path:
        warnings.append(
            "Latest approved recipe path differs from the source registry recipe_path; review which recipe should be used."
        )
    if status.approved_candidates and status.source_health_status == "untested":
        warnings.append("This source has approved recipe candidates, but source health is still untested.")
    if status.source_health_status == "good" and not status.execution_entry_exists:
        warnings.append(
            "Source health is good, but no daily-run projection exists yet. This is expected until enabled separately."
        )
    if status.source_health_status == "good" and status.execution_entry_exists and not status.execution_enabled:
        warnings.append(
            "Source health is good, but daily-run execution is disabled. Enablement remains a separate guarded step."
        )
    if latest and latest.status == "pending" and (not latest.schema_valid or latest.quality_status == "poor"):
        if candidate_is_reviewable(latest):
            warnings.append(
                "Latest pending candidate has invalid schema or poor local quality; review before approval."
            )
        else:
            warnings.append(
                "Latest generation attempt did not produce a usable reading plan; learn the source again with a better capture."
            )
    if latest and latest.status == "approved" and not latest.preview_saved:
        warnings.append("Latest approved candidate did not save preview health.")
    return warnings


def _candidate_matches_source(candidate: RecipeCandidate, source: SourceRegistryEntry, artifact_dirs: set[str]) -> bool:
    if candidate.source_name.strip().lower() == source.name.strip().lower():
        return True
    if source.url and candidate.start_url and _same_host_path(source.url, candidate.start_url):
        return True
    return bool(candidate.artifact_dir and candidate.artifact_dir in artifact_dirs)


def _same_host_path(left: str, right: str) -> bool:
    left_parsed = _parsed_url(left)
    right_parsed = _parsed_url(right)
    left_host = left_parsed.netloc.lower().removeprefix("www.")
    right_host = right_parsed.netloc.lower().removeprefix("www.")
    if not left_host or left_host != right_host:
        return False
    left_path = left_parsed.path.rstrip("/")
    right_path = right_parsed.path.rstrip("/")
    return not left_path or right_path == left_path or right_path.startswith(f"{left_path}/")


def _parsed_url(value: str):
    parsed = urlparse(value.strip())
    return parsed if parsed.netloc else urlparse(f"https://{value.strip()}")


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")
