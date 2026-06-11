from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.cli import adopt_approved_recipe
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.services.approved_recipe_adoption_service import ApprovedRecipeAdoptionService
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_suggestion_service import RecipeSuggestionResult
from job_agent.services.source_registry_service import SourceRegistryService

VALID_RECIPE_YAML = """source_name: Example Jobs
mode: static_html
listing:
  card_selector: article.job-card
  title_selector: a.job-link
  link_selector: a.job-link
limits:
  max_cards: 10
"""


def test_adopting_approved_candidate_updates_source_registry_recipe_path(project_root: Path) -> None:
    candidate = _approved_candidate(project_root, recipe_path="sources/recipes/experimental/new-eursap.yaml")

    result = ApprovedRecipeAdoptionService(project_root).adopt(candidate.candidate_id, "eursap-jobs")

    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    adopted = RecipeCandidateStore(project_root).load_candidate(candidate.candidate_id)
    assert result.registry_updated is True
    assert result.previous_recipe_path == "sources/recipes/experimental/eursap-jobs.yaml"
    assert source.recipe_path == "sources/recipes/experimental/new-eursap.yaml"
    assert "Adopted recipe from candidate" in source.notes
    assert adopted.adopted_source_id == "eursap-jobs"
    assert adopted.adopted_recipe_path == "sources/recipes/experimental/new-eursap.yaml"


def test_adoption_refuses_pending_and_rejected_candidates(project_root: Path) -> None:
    pending = _candidate(project_root)
    rejected = _candidate(project_root)
    RecipeCandidateStore(project_root).reject_candidate(rejected.candidate_id, reason="No")

    service = ApprovedRecipeAdoptionService(project_root)
    with pytest.raises(ValueError, match="Only approved"):
        service.adopt(pending.candidate_id, "eursap-jobs")
    with pytest.raises(ValueError, match="Only approved"):
        service.adopt(rejected.candidate_id, "eursap-jobs")


def test_adoption_refuses_approved_candidate_without_recipe_path(project_root: Path) -> None:
    candidate = _approved_candidate(project_root)
    data_path = RecipeCandidateStore(project_root).candidate_path(candidate.candidate_id)
    data = read_yaml(data_path, {})
    data["approved_recipe_path"] = ""
    write_yaml(data_path, data)

    with pytest.raises(ValueError, match="approved_recipe_path"):
        ApprovedRecipeAdoptionService(project_root).adopt(candidate.candidate_id, "eursap-jobs")


def test_adoption_refuses_missing_approved_recipe_file(project_root: Path) -> None:
    candidate = _approved_candidate(
        project_root, recipe_path="sources/recipes/experimental/missing.yaml", write_recipe=False
    )

    with pytest.raises(ValueError, match="missing"):
        ApprovedRecipeAdoptionService(project_root).adopt(candidate.candidate_id, "eursap-jobs")


def test_adoption_does_not_create_execution_entry_by_default(project_root: Path) -> None:
    candidate = _approved_candidate(project_root)

    ApprovedRecipeAdoptionService(project_root).adopt(candidate.candidate_id, "eursap-jobs")

    assert not (project_root / "sources" / "recruiting-sites.yaml").exists()


def test_prepare_disabled_execution_entry_creates_disabled_entry(project_root: Path) -> None:
    candidate = _approved_candidate(project_root)

    result = ApprovedRecipeAdoptionService(project_root).adopt(
        candidate.candidate_id,
        "eursap-jobs",
        prepare_disabled_execution_entry=True,
    )

    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    entry = config["sources"][0]
    assert result.execution_entry_created is True
    assert entry["source_id"] == "eursap-jobs"
    assert entry["recipe_path"] == "sources/recipes/experimental/new-eursap.yaml"
    assert entry["enabled"] is False


def test_prepare_disabled_execution_entry_updates_disabled_entry(project_root: Path) -> None:
    candidate = _approved_candidate(project_root)
    config_path = project_root / "sources" / "recruiting-sites.yaml"
    config_path.write_text(
        "sources:\n"
        "  - name: Eursap Jobs\n"
        "    source_id: eursap-jobs\n"
        "    type: recipe_html\n"
        "    url: https://eursap.eu/jobs\n"
        "    recipe_path: old.yaml\n"
        "    enabled: false\n",
        encoding="utf-8",
    )

    result = ApprovedRecipeAdoptionService(project_root).adopt(
        candidate.candidate_id,
        "eursap-jobs",
        prepare_disabled_execution_entry=True,
    )

    entry = read_yaml(config_path, {})["sources"][0]
    assert result.execution_entry_updated is True
    assert entry["recipe_path"] == "sources/recipes/experimental/new-eursap.yaml"
    assert entry["enabled"] is False


def test_prepare_disabled_execution_entry_overrides_stale_enabled_yaml_from_registry(project_root: Path) -> None:
    candidate = _approved_candidate(project_root)
    config_path = project_root / "sources" / "recruiting-sites.yaml"
    config_path.write_text(
        "sources:\n"
        "  - name: Eursap Jobs\n"
        "    source_id: eursap-jobs\n"
        "    type: recipe_html\n"
        "    url: https://eursap.eu/jobs\n"
        "    recipe_path: old.yaml\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    result = ApprovedRecipeAdoptionService(project_root).adopt(
        candidate.candidate_id,
        "eursap-jobs",
        prepare_disabled_execution_entry=True,
    )

    entry = read_yaml(config_path, {})["sources"][0]
    assert result.execution_entry_updated is True
    assert result.execution_entry_enabled_before is False
    assert entry["recipe_path"] == "sources/recipes/experimental/new-eursap.yaml"
    assert entry["enabled"] is False


def test_cli_adopt_approved_recipe_prints_summary(capsys, project_root: Path) -> None:
    candidate = _approved_candidate(project_root)

    adopt_approved_recipe(candidate.candidate_id, source_id="eursap-jobs", root=project_root)

    output = capsys.readouterr().out
    assert "Candidate adopted" in output
    assert "Previous registry recipe path" in output
    assert "Adopted recipe path: sources/recipes/experimental/new-eursap.yaml" in output
    assert "Execution was not enabled" in output


def _candidate(project_root: Path):
    artifact = project_root / "output" / "recipe-calibration" / "eursap"
    artifact.mkdir(parents=True, exist_ok=True)
    result = RecipeSuggestionResult(
        source_name="Eursap Jobs",
        start_url="https://eursap.eu/jobs",
        artifact_dir=artifact,
        suggested_recipe_yaml=VALID_RECIPE_YAML,
        schema_valid=True,
    )
    return RecipeCandidateStore(project_root).save_candidate_from_suggestion(result)


def _approved_candidate(
    project_root: Path,
    *,
    recipe_path: str = "sources/recipes/experimental/new-eursap.yaml",
    write_recipe: bool = True,
):
    candidate = _candidate(project_root)
    if write_recipe:
        path = project_root / recipe_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(VALID_RECIPE_YAML, encoding="utf-8")
    return RecipeCandidateStore(project_root).approve_candidate(
        candidate.candidate_id,
        recipe_path=recipe_path,
        source_id="eursap-jobs",
        preview_saved=True,
        preview_status="completed",
        preview_extracted_job_count=1,
        preview_useful_titles=1,
        preview_unique_urls=1,
    )
