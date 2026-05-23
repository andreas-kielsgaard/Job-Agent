from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.services.recipe_candidate_service import RecipeCandidate, RecipeCandidateStore
from job_agent.services.recipe_preview_service import RecipePreviewResult, preview_recipe
from job_agent.services.recipe_suggestion_service import validate_suggested_recipe_yaml
from job_agent.services.source_health_service import SourceHealthRecord, SourceHealthService


@dataclass
class RecipeCandidateApprovalResult:
    candidate: RecipeCandidate
    recipe_path: str
    preview: RecipePreviewResult | None = None
    health_record: SourceHealthRecord | None = None
    warnings: list[str] = field(default_factory=list)


class RecipeCandidateApprovalService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.store = RecipeCandidateStore(self.root)

    def approve(
        self,
        candidate_id: str,
        recipe_path: str,
        *,
        source_id: str = "",
        overwrite: bool = False,
        base_url: str = "",
    ) -> RecipeCandidateApprovalResult:
        candidate = self.store.load_candidate(candidate_id)
        if candidate.status != "pending":
            raise ValueError(f"Only pending recipe candidates can be approved. Current status: {candidate.status}.")
        if candidate.quality_status == "poor" or (candidate.refinement_used and not candidate.refinement_accepted):
            raise ValueError(
                "Candidate did not pass local extraction quality checks. Regenerate it from a better source capture."
            )

        validation_errors = validate_suggested_recipe_yaml(candidate.suggested_recipe_yaml)
        if validation_errors:
            raise ValueError("Candidate recipe YAML is not schema-valid: " + "; ".join(validation_errors))

        resolved_recipe_path = self._resolve_recipe_path(recipe_path)
        if resolved_recipe_path.exists() and not overwrite:
            raise ValueError(f"Recipe file already exists: {_display_path(resolved_recipe_path, self.root)}")

        artifact_dir = self._resolve_artifact_dir(candidate.artifact_dir)
        page_path = artifact_dir / "page.html"
        if not page_path.exists():
            raise ValueError(f"Candidate artifact is missing page.html: {_display_path(page_path, self.root)}")
        preview_base_url = base_url.strip() or candidate.start_url.strip()
        if not preview_base_url:
            raise ValueError("Approval preview requires a base URL from candidate.start_url or source URL.")

        resolved_recipe_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_recipe_path.write_text(candidate.suggested_recipe_yaml.strip() + "\n", encoding="utf-8")

        recipe_path_display = _display_path(resolved_recipe_path, self.root)
        page_path_display = _display_path(page_path, self.root)
        preview = preview_recipe(
            recipe_path_display,
            page_path_display,
            base_url=preview_base_url,
            static=True,
            root=self.root,
        )
        health_record = SourceHealthService(self.root).save_preview(source_id, preview) if source_id.strip() else None
        approved = self.store.approve_candidate(
            candidate_id,
            recipe_path=recipe_path_display,
            source_id=source_id,
            preview_saved=health_record is not None,
            preview_status="completed",
            preview_extracted_job_count=preview.extracted_job_count,
            preview_useful_titles=preview.useful_titles,
            preview_unique_urls=preview.unique_urls,
            preview_warnings=preview.warnings,
        )
        return RecipeCandidateApprovalResult(
            candidate=approved,
            recipe_path=recipe_path_display,
            preview=preview,
            health_record=health_record,
            warnings=list(preview.warnings),
        )

    def suggested_recipe_path(self, candidate: RecipeCandidate, source: Any = None) -> str:
        source_recipe_path = str(getattr(source, "recipe_path", "") or "").strip()
        if source_recipe_path:
            return source_recipe_path
        return f"sources/recipes/experimental/{_slug(candidate.source_name or candidate.candidate_id)}.yaml"

    def _resolve_recipe_path(self, recipe_path: str) -> Path:
        if not recipe_path.strip():
            raise ValueError("Approval requires an explicit recipe path.")
        path = Path(recipe_path)
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        base = (self.root / "sources" / "recipes").resolve()
        if resolved != base and base not in resolved.parents:
            raise ValueError("Recipe path must stay under sources/recipes.")
        if resolved.suffix.lower() not in {".yaml", ".yml"}:
            raise ValueError("Recipe path must be a YAML file under sources/recipes.")
        return resolved

    def _resolve_artifact_dir(self, artifact_dir: str) -> Path:
        path = Path(artifact_dir)
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        base = (self.root / "output" / "recipe-calibration").resolve()
        if resolved != base and base not in resolved.parents:
            raise ValueError("Candidate artifact path must stay under output/recipe-calibration.")
        if not resolved.is_dir():
            raise ValueError(f"Candidate artifact folder not found: {_display_path(resolved, self.root)}")
        return resolved


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "recipe-candidate")[:80].strip("-")
