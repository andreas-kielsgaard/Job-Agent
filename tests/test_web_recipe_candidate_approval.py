from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_suggestion_service import RecipeSuggestionResult
from job_agent.services.source_health_service import SourceHealthService
from job_agent.services.source_registry_service import SourceRegistryService
from tests.helpers import seed_common_sources

VALID_RECIPE_YAML = """source_name: Eursap Jobs
start_url: https://eursap.eu/jobs
mode: static_html
listing:
  card_selector: article.job-card
  title_selector: a.job-link
  link_selector: a.job-link
  description_selector: .description
accept:
  url_contains:
    - /jobs/
limits:
  max_cards: 10
"""


@pytest.fixture(autouse=True)
def configured_eursap_source(project_root: Path) -> None:
    seed_common_sources(project_root)


def test_candidate_detail_shows_approval_form_for_pending_candidate(client: TestClient, project_root: Path) -> None:
    candidate = _save_candidate(project_root)

    response = client.get(f"/recipe-candidates/{candidate.candidate_id}?source_id=eursap-jobs")

    assert response.status_code == 200
    assert "Review Reading Plan" in response.text
    assert "Use plan and run source test" in response.text
    assert 'value="sources/recipes/experimental/eursap-jobs.yaml"' in response.text
    assert "local extraction count is only a calibration sanity check" in response.text
    assert 'name="next_action" value="test"' in response.text
    assert 'name="overwrite" value="1"' in response.text


def test_web_approval_writes_recipe_saves_health_updates_candidate_and_redirects(
    client: TestClient, project_root: Path
) -> None:
    candidate = _save_candidate(project_root)

    response = client.post(
        f"/recipe-candidates/{candidate.candidate_id}/approve",
        data={
            "source_id": "eursap-jobs",
            "recipe_path": "sources/recipes/experimental/eursap-approved.yaml",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/sources/eursap-jobs")
    assert (project_root / "sources" / "recipes" / "experimental" / "eursap-approved.yaml").exists()
    approved = RecipeCandidateStore(project_root).load_candidate(candidate.candidate_id)
    assert approved.status == "approved"
    assert approved.approved_source_id == "eursap-jobs"
    assert approved.preview_saved is True
    assert approved.preview_extracted_job_count == 1
    assert approved.adopted_source_id == "eursap-jobs"
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    assert source.recipe_path == "sources/recipes/experimental/eursap-approved.yaml"
    health = SourceHealthService(project_root).get_health("eursap-jobs")
    assert health.extracted_job_count == 1
    assert health.health_status == "good"
    assert not (project_root / "sources" / "recruiting-sites.yaml").exists()


def test_web_approval_can_replace_current_recipe_and_redirect_to_source_test(
    client: TestClient, project_root: Path
) -> None:
    candidate = _save_candidate(project_root)
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text("source_name: Old\nlisting: {}\n", encoding="utf-8")

    response = client.post(
        f"/recipe-candidates/{candidate.candidate_id}/approve",
        data={
            "source_id": "eursap-jobs",
            "recipe_path": "sources/recipes/experimental/eursap-jobs.yaml",
            "overwrite": "1",
            "next_action": "test",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/sources/eursap-jobs/test-run?start=1"
    assert "article.job-card" in recipe_path.read_text(encoding="utf-8")
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    assert source.recipe_path == "sources/recipes/experimental/eursap-jobs.yaml"


def test_rejected_candidate_does_not_show_active_approval_form(client: TestClient, project_root: Path) -> None:
    store = RecipeCandidateStore(project_root)
    candidate = _save_candidate(project_root)
    store.reject_candidate(candidate.candidate_id, reason="Noisy")

    response = client.get(f"/recipe-candidates/{candidate.candidate_id}?source_id=eursap-jobs")

    assert response.status_code == 200
    assert "Reading plan discarded" in response.text
    assert "Use plan and run source test" not in response.text


def test_approved_candidate_does_not_show_active_approval_form(client: TestClient, project_root: Path) -> None:
    candidate = _save_candidate(project_root)
    client.post(
        f"/recipe-candidates/{candidate.candidate_id}/approve",
        data={"source_id": "eursap-jobs", "recipe_path": "sources/recipes/experimental/eursap-approved.yaml"},
        follow_redirects=False,
    )

    response = client.get(f"/recipe-candidates/{candidate.candidate_id}?source_id=eursap-jobs")

    assert response.status_code == 200
    assert "Reading plan saved" in response.text
    assert "Saved recipe path" in response.text
    assert "Use plan and run source test" not in response.text


def _save_candidate(project_root: Path):
    artifact = project_root / "output" / "recipe-calibration" / "eursap"
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "page.html").write_text(
        """
        <article class="job-card">
          <a class="job-link" href="/jobs/sap-basis">SAP Basis Consultant</a>
          <p class="description">SAP Basis contract role supporting an S/4HANA migration with operations work.</p>
        </article>
        """,
        encoding="utf-8",
    )
    result = RecipeSuggestionResult(
        source_name="Eursap Jobs",
        start_url="https://eursap.eu/jobs",
        artifact_dir=artifact,
        suggested_recipe_yaml=VALID_RECIPE_YAML,
        schema_valid=True,
    )
    return RecipeCandidateStore(project_root).save_candidate_from_suggestion(result)
