from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_suggestion_service import (
    RecipeRefinementAttempt,
    RecipeRefinementResult,
    RecipeSuggestionResult,
)

VALID_RECIPE_YAML = """source_name: Eursap Jobs
start_url: https://eursap.eu/jobs
mode: static_html
listing:
  card_selector: a.looking__card
  title_selector: a.looking__card
  link_selector: a.looking__card
limits:
  max_cards: 10
"""


def test_source_detail_displays_recipe_generation_controls_and_artifacts(client: TestClient, project_root: Path) -> None:
    _write_artifact(project_root)

    response = client.get("/sources/eursap-jobs")

    assert response.status_code == 200
    assert "Generate or replace recipe" in response.text
    assert "Save draft recipe for review" in response.text
    assert "eursap-artifact" in response.text
    assert "a.looking__card" in response.text
    assert "--save-candidate" in response.text


def test_generate_candidate_plain_suggestion_saves_pending_candidate(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    artifact = _write_artifact(project_root)

    monkeypatch.setattr(
        "job_agent.web.routers.sources.suggest_recipe_from_artifact",
        lambda *args, **kwargs: _suggestion(artifact),
    )

    response = client.post(
        "/sources/eursap-jobs/recipe-candidates/generate",
        data={"artifact_dir": artifact.relative_to(project_root).as_posix()},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Pending+recipe+candidate+saved" in response.headers["location"]
    candidates = RecipeCandidateStore(project_root).list_candidates()
    assert len(candidates) == 1
    candidate = RecipeCandidateStore(project_root).load_candidate(candidates[0].candidate_id)
    assert candidate.status == "pending"
    assert candidate.refinement_used is False


def test_generate_candidate_with_refinement_saves_attempt_history(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    artifact = _write_artifact(project_root)

    monkeypatch.setattr(
        "job_agent.web.routers.sources.suggest_recipe_with_refinement",
        lambda *args, **kwargs: _refinement(artifact),
    )

    response = client.post(
        "/sources/eursap-jobs/recipe-candidates/generate",
        data={"artifact_dir": artifact.relative_to(project_root).as_posix(), "refine": "1", "max_attempts": "3"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    candidate_id = RecipeCandidateStore(project_root).list_candidates()[0].candidate_id
    candidate = RecipeCandidateStore(project_root).load_candidate(candidate_id)
    assert candidate.refinement_used is True
    assert candidate.refinement_accepted is True
    assert candidate.attempt_count == 1
    assert candidate.quality_status == "good"
    assert candidate.extracted_job_count == 2


def test_generate_candidate_validates_artifact_path(client: TestClient) -> None:
    response = client.post(
        "/sources/eursap-jobs/recipe-candidates/generate",
        data={"artifact_dir": "sources/source-registry.yaml"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "warning=" in response.headers["location"]


def test_generate_candidate_handles_llm_unavailable_as_redirect_warning(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    artifact = _write_artifact(project_root)

    def unavailable(*args, **kwargs):
        raise RuntimeError("ANTHROPIC_API_KEY is missing or placeholder.")

    monkeypatch.setattr("job_agent.web.routers.sources.suggest_recipe_from_artifact", unavailable)

    response = client.post(
        "/sources/eursap-jobs/recipe-candidates/generate",
        data={"artifact_dir": artifact.relative_to(project_root).as_posix()},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "warning=" in response.headers["location"]
    assert not RecipeCandidateStore(project_root).list_candidates()


def test_source_detail_displays_relevant_pending_and_rejected_candidates(
    client: TestClient, project_root: Path
) -> None:
    artifact = _write_artifact(project_root)
    store = RecipeCandidateStore(project_root)
    pending = store.save_candidate_from_suggestion(_suggestion(artifact))
    rejected = store.save_candidate_from_suggestion(_suggestion(artifact))
    store.reject_candidate(rejected.candidate_id, reason="Wrong block")

    response = client.get("/sources/eursap-jobs")

    assert response.status_code == 200
    assert pending.candidate_id in response.text
    assert rejected.candidate_id in response.text
    assert "Draft recipes" in response.text
    assert "rejected" in response.text


def test_candidate_detail_page_shows_yaml_and_attempt_history(client: TestClient, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    candidate = RecipeCandidateStore(project_root).save_candidate_from_refinement(_refinement(artifact))

    response = client.get(f"/recipe-candidates/{candidate.candidate_id}?source_id=eursap-jobs")

    assert response.status_code == 200
    assert "Suggested YAML" in response.text
    assert "a.looking__card" in response.text
    assert "Attempt 1" in response.text
    assert "Back to Eursap Jobs" in response.text


def test_reject_candidate_from_ui_updates_status(client: TestClient, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    candidate = RecipeCandidateStore(project_root).save_candidate_from_suggestion(_suggestion(artifact))

    response = client.post(
        f"/recipe-candidates/{candidate.candidate_id}/reject",
        data={"source_id": "eursap-jobs", "reason": "Not stable"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/sources/eursap-jobs")
    loaded = RecipeCandidateStore(project_root).load_candidate(candidate.candidate_id)
    assert loaded.status == "rejected"
    assert loaded.rejection_reason == "Not stable"


def _write_artifact(project_root: Path) -> Path:
    artifact = project_root / "output" / "recipe-calibration" / "eursap-artifact"
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "summary.md").write_text("# Eursap\n", encoding="utf-8")
    (artifact / "page.html").write_text('<a class="looking__card" href="/jobs/sap">SAP Basis Consultant</a>', encoding="utf-8")
    (artifact / "selector-report.json").write_text(
        json.dumps(
            {
                "url": "https://eursap.eu/jobs",
                "capture_mode": "static_html",
                "candidates": [{"selector": "a.looking__card"}],
            }
        ),
        encoding="utf-8",
    )
    return artifact


def _suggestion(artifact: Path) -> RecipeSuggestionResult:
    return RecipeSuggestionResult(
        source_name="Eursap Jobs",
        start_url="https://eursap.eu/jobs",
        artifact_dir=artifact,
        suggested_recipe_yaml=VALID_RECIPE_YAML,
        confidence="high",
        selected_strategy="selector_based",
        evidence_summary="candidate selectors: a.looking__card",
        referenced_artifact_files=["summary.md", "selector-report.json", "page.html"],
        schema_valid=True,
    )


def _refinement(artifact: Path) -> RecipeRefinementResult:
    return RecipeRefinementResult(
        final_result=_suggestion(artifact),
        attempts=[
            RecipeRefinementAttempt(
                attempt_number=1,
                suggested_recipe_yaml=VALID_RECIPE_YAML,
                schema_valid=True,
                validation_errors=[],
                quality_status="good",
                quality_warnings=[],
                extracted_job_count=2,
                useful_titles=2,
                generic_labels=0,
                unique_urls=2,
                average_description_length=100,
            )
        ],
        accepted=True,
    )
