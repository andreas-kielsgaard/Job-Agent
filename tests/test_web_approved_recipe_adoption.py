from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from job_agent.io.yaml_store import read_yaml
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_suggestion_service import RecipeSuggestionResult
from job_agent.services.source_registry_service import SourceRegistryService

VALID_RECIPE_YAML = """source_name: Eursap Jobs
mode: static_html
listing:
  card_selector: article.job-card
  title_selector: a.job-link
  link_selector: a.job-link
limits:
  max_cards: 10
"""


def test_candidate_detail_shows_adoption_form_only_for_approved_candidates(client: TestClient, project_root: Path) -> None:
    pending = _candidate(project_root)
    approved = _approved_candidate(project_root)

    pending_response = client.get(f"/recipe-candidates/{pending.candidate_id}?source_id=eursap-jobs")
    approved_response = client.get(f"/recipe-candidates/{approved.candidate_id}?source_id=eursap-jobs")

    assert "Adopt For Source" not in pending_response.text
    assert "Adopt For Source" in approved_response.text
    assert "Prepare disabled execution entry" in approved_response.text


def test_web_adoption_updates_registry_and_redirects(client: TestClient, project_root: Path) -> None:
    candidate = _approved_candidate(project_root)

    response = client.post(
        f"/recipe-candidates/{candidate.candidate_id}/adopt",
        data={"source_id": "eursap-jobs"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/sources/eursap-jobs")
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    assert source.recipe_path == "sources/recipes/experimental/new-eursap.yaml"
    assert not (project_root / "sources" / "recruiting-sites.yaml").exists()


def test_web_adoption_can_prepare_disabled_execution_entry(client: TestClient, project_root: Path) -> None:
    candidate = _approved_candidate(project_root)

    response = client.post(
        f"/recipe-candidates/{candidate.candidate_id}/adopt",
        data={"source_id": "eursap-jobs", "prepare_disabled_execution_entry": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    assert config["sources"][0]["source_id"] == "eursap-jobs"
    assert config["sources"][0]["enabled"] is False


def test_source_detail_mismatch_warning_disappears_after_adoption(client: TestClient, project_root: Path) -> None:
    candidate = _approved_candidate(project_root)
    before = client.get("/sources/eursap-jobs")
    assert "differs from the source registry recipe_path" in before.text

    client.post(
        f"/recipe-candidates/{candidate.candidate_id}/adopt",
        data={"source_id": "eursap-jobs"},
        follow_redirects=False,
    )
    after = client.get("/sources/eursap-jobs")

    assert "differs from the source registry recipe_path" not in after.text


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


def _approved_candidate(project_root: Path):
    candidate = _candidate(project_root)
    recipe_path = "sources/recipes/experimental/new-eursap.yaml"
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
