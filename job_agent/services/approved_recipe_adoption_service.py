from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from job_agent.config import ROOT
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.recipe_candidate_service import RecipeCandidate, RecipeCandidateStore
from job_agent.services.source_registry_service import SourceRegistryService


@dataclass
class ApprovedRecipeAdoptionResult:
    candidate: RecipeCandidate
    source_id: str
    source_name: str
    previous_recipe_path: str
    adopted_recipe_path: str
    registry_updated: bool = False
    execution_entry_created: bool = False
    execution_entry_updated: bool = False
    execution_entry_enabled_before: bool = False
    warnings: list[str] = field(default_factory=list)


class ApprovedRecipeAdoptionService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.candidates = RecipeCandidateStore(self.root)
        self.registry = SourceRegistryService(self.root)
        self.execution = ExecutionSourceService(self.root)

    def adopt(
        self,
        candidate_id: str,
        source_id: str,
        *,
        prepare_disabled_execution_entry: bool = False,
    ) -> ApprovedRecipeAdoptionResult:
        source_id = source_id.strip()
        if not source_id:
            raise ValueError("Adoption requires a source id.")
        candidate = self.candidates.load_candidate(candidate_id)
        if candidate.status != "approved":
            raise ValueError(f"Only approved recipe candidates can be adopted. Current status: {candidate.status}.")
        if not candidate.approved_recipe_path.strip():
            raise ValueError("Approved candidate has no approved_recipe_path.")
        recipe_path = self._validate_approved_recipe_path(candidate.approved_recipe_path)
        source = self.registry.get_source(source_id)
        if not source:
            raise ValueError(f"Source not found: {source_id}")
        if source.kind not in {"recipe", "experimental_recipe"} and not source.recipe_path:
            raise ValueError("Only recipe-backed sources can adopt approved recipes.")

        warnings = []
        if candidate.approved_source_id and candidate.approved_source_id != source_id:
            warnings.append(
                f"Candidate was approved for source_id {candidate.approved_source_id}, but adopted for {source_id}."
            )
        if not candidate.preview_saved:
            warnings.append("Candidate approval did not save preview health.")

        previous_recipe_path = source.recipe_path
        note = f"Adopted recipe from candidate {candidate.candidate_id}."
        updated_source = self.registry.adopt_recipe_path(source_id, recipe_path, note=note)

        execution_created = False
        execution_updated = False
        execution_enabled_before = False
        if prepare_disabled_execution_entry:
            existing_entry = self.execution.find_by_source_id(source_id)
            execution_enabled_before = bool(existing_entry and existing_entry.get("enabled", True))
            if execution_enabled_before:
                raise ValueError("Execution entry is enabled; disable it before refreshing from adopted recipe.")
            execution_result = self.execution.create_or_update_recipe_source(updated_source, enabled=False)
            execution_created = execution_result.created
            execution_updated = execution_result.updated

        adopted = self.candidates.adopt_candidate(
            candidate_id,
            source_id=source_id,
            recipe_path=recipe_path,
            execution_entry_created=execution_created,
            execution_entry_updated=execution_updated,
        )
        return ApprovedRecipeAdoptionResult(
            candidate=adopted,
            source_id=source_id,
            source_name=updated_source.name,
            previous_recipe_path=previous_recipe_path,
            adopted_recipe_path=recipe_path,
            registry_updated=True,
            execution_entry_created=execution_created,
            execution_entry_updated=execution_updated,
            execution_entry_enabled_before=execution_enabled_before,
            warnings=warnings,
        )

    def _validate_approved_recipe_path(self, value: str) -> str:
        path = Path(value)
        if path.is_absolute():
            resolved = path.resolve()
        else:
            resolved = (self.root / path).resolve()
        base = (self.root / "sources" / "recipes").resolve()
        if resolved != base and base not in resolved.parents:
            raise ValueError("Approved recipe path must stay under sources/recipes.")
        if not resolved.exists():
            raise ValueError(f"Approved recipe file is missing: {_display_path(resolved, self.root)}")
        return _display_path(resolved, self.root)


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
