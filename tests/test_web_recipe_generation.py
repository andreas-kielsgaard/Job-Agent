from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_generation_run_service import RecipeGenerationRunService
from job_agent.services.recipe_suggestion_service import (
    RecipeRefinementAttempt,
    RecipeRefinementResult,
    RecipeSuggestionResult,
)
from job_agent.services.source_registry_service import SourceRegistryService

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


def test_source_detail_displays_recipe_generation_controls_and_artifacts(
    client: TestClient, project_root: Path
) -> None:
    _write_artifact(project_root)

    response = client.get("/sources/eursap-jobs")

    assert response.status_code == 200
    assert "Advanced: regenerate or inspect plans" in response.text
    assert "Generate plan from saved sample" in response.text
    assert "eursap-artifact" in response.text
    assert "a.looking__card" in response.text


def test_generate_candidate_plain_suggestion_saves_pending_candidate(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    artifact = _write_artifact(project_root)

    monkeypatch.setattr(
        "job_agent.services.recipe_generation_run_service.suggest_recipe_from_artifact",
        lambda *args, **kwargs: _suggestion(artifact),
    )

    response = client.post(
        "/sources/eursap-jobs/recipe-candidates/generate",
        data={"artifact_dir": artifact.relative_to(project_root).as_posix()},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/sources/eursap-jobs/recipe-generation/" in response.headers["location"]
    run = _wait_for_generation_run(client, response.headers["location"])
    assert run["status"] == "completed"
    assert run["candidate_id"]
    assert run["candidate_approval_url"].endswith("/approve")
    assert run["approval_recipe_path"] == "sources/recipes/experimental/eursap-jobs.yaml"
    assert run["compatibility_url"]
    assert run["recipe_review_url"]
    assert (project_root / run["generated_recipe_path"]).exists()
    candidates = RecipeCandidateStore(project_root).list_candidates()
    assert len(candidates) == 1
    candidate = RecipeCandidateStore(project_root).load_candidate(candidates[0].candidate_id)
    assert candidate.status == "pending"
    assert candidate.refinement_used is False
    page = client.get(response.headers["location"])
    assert "Use plan and run source test" in page.text
    assert "Open local calibration preview" in page.text
    assert "safe source test is the next verification step" in page.text


def test_generate_candidate_with_refinement_saves_attempt_history(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    artifact = _write_artifact(project_root)

    monkeypatch.setattr(
        "job_agent.services.recipe_generation_run_service.suggest_recipe_with_refinement",
        lambda *args, **kwargs: _refinement(artifact),
    )

    response = client.post(
        "/sources/eursap-jobs/recipe-candidates/generate",
        data={"artifact_dir": artifact.relative_to(project_root).as_posix(), "refine": "1", "max_attempts": "3"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    run = _wait_for_generation_run(client, response.headers["location"])
    assert run["status"] == "completed"
    assert run["attempt_count"] == 1
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

    monkeypatch.setattr("job_agent.services.recipe_generation_run_service.suggest_recipe_from_artifact", unavailable)

    response = client.post(
        "/sources/eursap-jobs/recipe-candidates/generate",
        data={"artifact_dir": artifact.relative_to(project_root).as_posix()},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/sources/eursap-jobs/recipe-generation/" in response.headers["location"]
    run = _wait_for_generation_run(client, response.headers["location"])
    assert run["status"] == "failed"
    assert "ANTHROPIC_API_KEY" in run["error"]
    assert not RecipeCandidateStore(project_root).list_candidates()


def test_learn_source_uses_auto_capture_by_default(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    source = SourceRegistryService(project_root).add_source(
        name="Experis",
        url="https://www.experis.pl/en/search?page=1&searchKeyword=SAP",
    )
    captured: dict[str, object] = {}

    class RunService:
        def start_from_source_capture(self, source_id: str, **kwargs):
            captured.update(kwargs)
            return {"run_id": "run-1"}

    monkeypatch.setattr("job_agent.web.workflows.RecipeGenerationRunService", lambda root: RunService())

    response = client.post(f"/sources/{source.id}/reading-plan/learn", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/sources/{source.id}/recipe-generation/run-1"
    assert captured["rendered"] is None
    assert captured["capture_detail"] is True


def test_generation_service_passes_auto_rendered_mode_to_calibration(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    source = SourceRegistryService(project_root).add_source(name="Example Jobs", url="https://example.com/jobs")
    artifact = _write_artifact(project_root)
    captured: dict[str, object] = {}

    def fake_capture(url, recipe_path, rendered, root, max_candidates, capture_detail):
        captured.update({"url": url, "recipe_path": recipe_path, "rendered": rendered})
        return SimpleNamespace(
            artifact_dir=artifact,
            warnings=[],
            candidate_count=1,
            recipe_extracted_count=0,
            detail_sample_url="",
        )

    monkeypatch.setattr("job_agent.services.recipe_generation_run_service.capture_recipe_calibration", fake_capture)
    monkeypatch.setattr(
        "job_agent.services.recipe_generation_run_service.suggest_recipe_with_refinement",
        lambda artifact_path, **kwargs: _refinement(artifact_path),
    )

    run = RecipeGenerationRunService(project_root).start_from_source_capture(
        source.id,
        rendered=None,
        run_async=False,
    )

    assert run["status"] == "completed"
    assert captured["url"] == "https://example.com/jobs"
    assert captured["rendered"] is None


def test_generation_service_uses_rendered_capture_for_client_side_pagination_insight(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    source = SourceRegistryService(project_root).add_source(name="Example Jobs", url="https://example.com/jobs")
    artifact = _write_artifact(project_root)
    captured: dict[str, object] = {}

    def fake_capture(url, recipe_path, rendered, root, max_candidates, capture_detail):
        captured.update({"url": url, "recipe_path": recipe_path, "rendered": rendered})
        return SimpleNamespace(
            artifact_dir=artifact,
            warnings=[],
            candidate_count=1,
            recipe_extracted_count=0,
            detail_sample_url="",
        )

    monkeypatch.setattr("job_agent.services.recipe_generation_run_service.capture_recipe_calibration", fake_capture)
    monkeypatch.setattr(
        "job_agent.services.recipe_generation_run_service.suggest_recipe_with_refinement",
        lambda artifact_path, **kwargs: _refinement(artifact_path),
    )

    run = RecipeGenerationRunService(project_root).start_from_source_capture(
        source.id,
        rendered=None,
        source_test_insight={
            "insight_title": "Paginated page access failed",
            "pagination_strategy_tested": "url",
            "pagination_duplicate_ratio": 1.0,
            "failed_capabilities": [
                {"detail": "Later pages may require a logged-in session or client-side pagination."}
            ],
        },
        run_async=False,
    )

    assert run["status"] == "completed"
    assert captured["url"] == "https://example.com/jobs"
    assert captured["rendered"] is True


def test_generated_draft_recipe_is_available_in_follow_up_dropdowns(client: TestClient, project_root: Path) -> None:
    recipe_path = project_root / "output" / "recipe-generation-runs" / "example-run" / "suggested-recipe.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(VALID_RECIPE_YAML, encoding="utf-8")
    relative = recipe_path.relative_to(project_root).as_posix()

    preview_response = client.get(
        f"/recipe-preview?recipe_path={relative}&input_path_or_url=https://eursap.eu/jobs&selected_source_id=eursap-jobs"
    )
    compatibility_response = client.get(
        f"/compatibility?recipe_path={relative}&url=https://eursap.eu/jobs&selected_source_id=eursap-jobs"
    )

    assert preview_response.status_code == 200
    assert compatibility_response.status_code == 200
    assert "Generated draft: suggested-recipe.yaml" in preview_response.text
    assert "Generated draft: suggested-recipe.yaml" in compatibility_response.text
    assert f'<option value="{relative}" selected>' in preview_response.text
    assert f'<option value="{relative}" selected>' in compatibility_response.text


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
    assert "Generated plans" in response.text
    assert "rejected" in response.text


def test_source_detail_does_not_treat_unusable_attempt_as_reviewable_plan(
    client: TestClient, project_root: Path
) -> None:
    artifact = _write_artifact(project_root)
    source = SourceRegistryService(project_root).add_source(
        name="Experis",
        url="https://www.experis.pl/en/search?page=1&searchKeyword=SAP",
    )
    candidate = RecipeCandidateStore(project_root).save_candidate_from_suggestion(
        RecipeSuggestionResult(
            source_name="Experis",
            start_url=source.url,
            artifact_dir=artifact,
            suggested_recipe_yaml="",
            schema_valid=False,
            validation_errors=["No stable repeated listing card selector was found."],
            selected_strategy="not_recommended",
            confidence="low",
        )
    )

    response = client.get(f"/sources/{source.id}")

    assert response.status_code == 200
    assert "Teach the app how to read this source" in response.text
    assert "Review generated reading plan" not in response.text
    assert "Review latest generated plan" not in response.text
    assert candidate.candidate_id in response.text


def test_candidate_detail_page_shows_yaml_and_attempt_history(client: TestClient, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    candidate = RecipeCandidateStore(project_root).save_candidate_from_refinement(_refinement(artifact))

    response = client.get(f"/recipe-candidates/{candidate.candidate_id}?source_id=eursap-jobs")

    assert response.status_code == 200
    assert "Generated YAML" in response.text
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
    (artifact / "page.html").write_text(
        '<a class="looking__card" href="/jobs/sap">SAP Basis Consultant</a>', encoding="utf-8"
    )
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


def _wait_for_generation_run(client: TestClient, location: str) -> dict:
    status_url = f"{location}/status"
    for _ in range(40):
        response = client.get(status_url)
        assert response.status_code == 200
        data = response.json()
        if data["status"] in {"completed", "failed"}:
            return data
        time.sleep(0.05)
    raise AssertionError("Recipe generation run did not finish.")


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
