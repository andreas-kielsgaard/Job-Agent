from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from job_agent.config import ROOT
from job_agent.services.recipe_artifact_service import (
    RecipeArtifactService,
    RecipeArtifactSummary,
)

SELECTOR_FIELDS = [
    ("listing.card_selector", "Job card", "Repeated container for one job listing.", True),
    ("listing.title_selector", "Listing title", "Text used as the job title on the listing page.", True),
    ("listing.link_selector", "Listing URL", "Link that points to the job detail page.", True),
    ("listing.company_selector", "Company", "Company or recruiter text on the listing card.", False),
    ("listing.location_selector", "Location", "Location text on the listing card.", False),
    ("listing.remote_selector", "Remote", "Remote, hybrid, or onsite text.", False),
    ("listing.rate_selector", "Rate", "Pay or rate text on the listing card.", False),
    ("listing.workload_selector", "Workload", "Contract type, full-time, or workload text.", False),
    ("listing.posted_date_selector", "Posted date", "Date text on the listing card.", False),
    ("listing.description_selector", "Listing description", "Summary text on the listing card.", False),
    ("detail.title_selector", "Detail title", "Title on the detail page.", False),
    ("detail.description_selector", "Detail description", "Main description on the detail page.", False),
    ("detail.location_selector", "Detail location", "Location on the detail page.", False),
    ("detail.remote_selector", "Detail remote", "Remote, hybrid, or onsite text on the detail page.", False),
    ("detail.rate_selector", "Detail rate", "Pay or rate text on the detail page.", False),
    ("detail.workload_selector", "Detail workload", "Contract type, full-time, or workload text on the detail page.", False),
    ("detail.posted_date_selector", "Detail posted date", "Posted-date text on the detail page.", False),
    ("detail.start_date_selector", "Detail start date", "Start-date text on the detail page.", False),
    ("detail.language_selector", "Detail language", "Language requirement text on the detail page.", False),
    ("pagination.page_link_selector", "Pagination links", "Links for numbered listing pages.", False),
    ("pagination.next_selector", "Next-page link", "Link to the next listing page.", False),
]


@dataclass
class RecipeSelectorField:
    path: str
    label: str
    help_text: str
    required: bool
    value: str = ""

    @property
    def input_name(self) -> str:
        return f"selector__{self.path.replace('.', '__')}"


@dataclass
class RecipeEditorState:
    recipe_path: str = ""
    artifact_dir: str = ""
    recipe_name: str = ""
    start_url: str = ""
    mode: str = ""
    fields: list[RecipeSelectorField] = field(default_factory=list)
    artifacts: list[RecipeArtifactSummary] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_yaml: str = ""


class RecipeEditorService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.artifacts = RecipeArtifactService(self.root)

    def load(self, recipe_path: str = "", artifact_dir: str = "") -> RecipeEditorState:
        recipes = self._recipe_paths()
        selected_recipe = recipe_path.strip() or (recipes[0] if recipes else "")
        artifacts = self.artifacts.list_artifacts()
        selected_artifact = artifact_dir.strip() or _first_page_artifact(artifacts)
        state = RecipeEditorState(recipe_path=selected_recipe, artifact_dir=selected_artifact, artifacts=artifacts)
        if not selected_recipe:
            state.warnings.append("No recipe files found under sources/recipes.")
            return state

        path = self.resolve_recipe_path(selected_recipe)
        try:
            data = _read_recipe_mapping(path)
        except ValueError as exc:
            state.warnings.append(str(exc))
            return state

        state.raw_yaml = path.read_text(encoding="utf-8")
        state.recipe_name = str(data.get("source_name") or path.stem.replace("-", " ").title())
        state.start_url = str(data.get("start_url") or "")
        state.mode = str(data.get("mode") or "")
        state.fields = [
            RecipeSelectorField(
                path=field_path,
                label=label,
                help_text=help_text,
                required=required,
                value=_selector_text(_nested_get(data, field_path)),
            )
            for field_path, label, help_text, required in SELECTOR_FIELDS
        ]
        return state

    def save_selectors(self, recipe_path: str, values: dict[str, str]) -> None:
        path = self.resolve_recipe_path(recipe_path)
        data = _read_recipe_mapping(path)
        for field_path, _label, _help_text, required in SELECTOR_FIELDS:
            if field_path not in values:
                continue
            value = values.get(field_path, "")
            _nested_set(data, field_path, _selector_value(value, single=field_path == "listing.card_selector", required=required))
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")

    def resolve_recipe_path(self, value: str) -> Path:
        if not value.strip():
            raise ValueError("Select a recipe.")
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve()
            recipes_root = (self.root / "sources" / "recipes").resolve()
        except OSError as exc:
            raise ValueError(f"Invalid recipe path: {value}") from exc
        if resolved != recipes_root and recipes_root not in resolved.parents:
            raise ValueError("Recipe path must stay under sources/recipes.")
        if not resolved.exists():
            raise ValueError(f"Recipe not found: {value}")
        return resolved

    def resolve_artifact_page(self, value: str) -> Path:
        artifact = self.artifacts.resolve_artifact_path(value)
        page = artifact / "page.html"
        if not page.exists():
            raise ValueError(f"Artifact has no page.html: {value}")
        return page

    def _recipe_paths(self) -> list[str]:
        recipes_root = self.root / "sources" / "recipes"
        if not recipes_root.exists():
            return []
        paths = []
        for path in sorted(recipes_root.rglob("*.yaml")):
            relative = path.relative_to(self.root).as_posix()
            if "/examples/" in f"/{relative}":
                continue
            paths.append(relative)
        return paths


def _first_page_artifact(artifacts: list[RecipeArtifactSummary]) -> str:
    return next((artifact.artifact_dir for artifact in artifacts if artifact.has_page_html), "")


def _read_recipe_mapping(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Recipe YAML is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Recipe YAML must be a mapping.")
    return data


def _nested_get(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part, "")
    return current


def _nested_set(data: dict[str, Any], path: str, value: Any) -> None:
    current = data
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _selector_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _selector_value(value: str, *, single: bool, required: bool) -> str | list[str]:
    lines = [line.strip() for line in value.replace(",", "\n").splitlines() if line.strip()]
    if single:
        return lines[0] if lines else ""
    if not lines:
        return "" if not required else ""
    if len(lines) == 1:
        return lines[0]
    return lines
