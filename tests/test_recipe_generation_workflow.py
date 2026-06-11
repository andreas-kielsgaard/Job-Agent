from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from job_agent.cli import recipe_generation_status
from job_agent.services.recipe_candidate_approval_service import RecipeCandidateApprovalService
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_generation_run_service import RecipeGenerationRunService
from job_agent.services.recipe_generation_status_service import RecipeGenerationStatusService
from job_agent.services.recipe_suggestion_service import (
    RecipeRefinementAttempt,
    RecipeRefinementResult,
    RecipeSuggestionLlmClient,
    RecipeSuggestionResult,
    suggest_recipe_with_refinement,
)
from job_agent.services.source_health_service import SourceHealthService
from job_agent.services.source_session_service import SourceSessionService

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


class FakeLlm(RecipeSuggestionLlmClient):
    def __init__(self, recipe_yaml: str) -> None:
        self.recipe_yaml = recipe_yaml
        self.prompts: list[str] = []

    def suggest(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(
            {
                "suggested_recipe_yaml": self.recipe_yaml,
                "explanation": "Use local job cards.",
                "confidence": "high",
                "assumptions": ["Saved artifact contains the repeated listing blocks."],
                "warnings": [],
                "selected_strategy": "selector_based",
            }
        )


def test_local_auto_recipe_workflow_end_to_end_without_execution_enablement(project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    before_execution_config = _read_optional(project_root / "sources" / "recruiting-sites.yaml")

    refinement = suggest_recipe_with_refinement(
        artifact,
        source_name="Eursap Jobs",
        start_url="https://eursap.eu/jobs",
        llm_client=FakeLlm(VALID_RECIPE_YAML),
        max_attempts=3,
        root=project_root,
    )
    candidate = RecipeCandidateStore(project_root).save_candidate_from_refinement(refinement)
    approved = RecipeCandidateApprovalService(project_root).approve(
        candidate.candidate_id,
        "sources/recipes/experimental/eursap-jobs.yaml",
        source_id="eursap-jobs",
    )

    recipe_path = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    stored = RecipeCandidateStore(project_root).load_candidate(candidate.candidate_id)
    health = SourceHealthService(project_root).get_health("eursap-jobs")
    status = RecipeGenerationStatusService(project_root).build_for_source("eursap-jobs")

    assert recipe_path.exists()
    assert stored.status == "approved"
    assert approved.preview is not None
    assert approved.preview.extracted_job_count == 1
    assert health.health_status == "good"
    assert status.latest_approved_recipe_path == "sources/recipes/experimental/eursap-jobs.yaml"
    assert status.approved_matches_source_recipe_path is True
    assert status.execution_entry_exists is True
    assert status.execution_enabled is False
    assert _read_optional(project_root / "sources" / "recruiting-sites.yaml") == before_execution_config


def test_source_detail_shows_lifecycle_status_and_mismatch_warning(client: TestClient, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    candidate = RecipeCandidateStore(project_root).save_candidate_from_suggestion(
        _suggestion_result(artifact, source_name="Eursap Jobs")
    )
    RecipeCandidateApprovalService(project_root).approve(
        candidate.candidate_id,
        "sources/recipes/experimental/generated-eursap.yaml",
        source_id="eursap-jobs",
    )

    response = client.get("/sources/eursap-jobs")

    assert response.status_code == 200
    assert "Generated plans" in response.text
    assert "generated-eursap.yaml" in response.text
    assert "differs from the source registry recipe_path" in response.text
    assert "Plan notes" in response.text


def test_generation_status_separates_unusable_attempts_from_reviewable_plans(project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    store = RecipeCandidateStore(project_root)
    unusable = store.save_candidate_from_suggestion(_unusable_suggestion_result(artifact))
    rejected = store.save_candidate_from_suggestion(_suggestion_result(artifact))
    store.reject_candidate(rejected.candidate_id, reason="Wrong block")

    status = RecipeGenerationStatusService(project_root).build_for_source("eursap-jobs")

    assert status.pending_candidates == 1
    assert status.rejected_candidates == 1
    assert status.latest_candidate_id in {unusable.candidate_id, rejected.candidate_id}
    assert status.reviewable_pending_candidates == 0
    assert status.latest_reviewable_candidate_id == ""
    assert unusable.candidate_id != status.latest_reviewable_candidate_id


def test_approved_candidate_detail_shows_summary_and_no_approval_form(client: TestClient, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    candidate = RecipeCandidateStore(project_root).save_candidate_from_suggestion(_suggestion_result(artifact))
    RecipeCandidateApprovalService(project_root).approve(
        candidate.candidate_id,
        "sources/recipes/experimental/eursap-jobs.yaml",
        source_id="eursap-jobs",
    )

    response = client.get(f"/recipe-candidates/{candidate.candidate_id}?source_id=eursap-jobs")

    assert response.status_code == 200
    assert "Reading plan saved" in response.text
    assert "Technical details" in response.text
    assert "Use plan and run source test" not in response.text


def test_recipe_generation_status_cli_prints_workflow_state(capsys, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    candidate = RecipeCandidateStore(project_root).save_candidate_from_suggestion(_suggestion_result(artifact))
    RecipeCandidateApprovalService(project_root).approve(
        candidate.candidate_id,
        "sources/recipes/experimental/eursap-jobs.yaml",
        source_id="eursap-jobs",
    )

    recipe_generation_status("eursap-jobs", root=project_root)

    output = capsys.readouterr().out
    assert "Source: Eursap Jobs (eursap-jobs)" in output
    assert "Calibration artifacts: 1" in output
    assert "Candidates: pending=0, approved=1, rejected=0" in output
    assert "Source health: good" in output
    assert "Daily-run enabled: False" in output
    assert "does not mutate anything" in output


def test_recipe_generation_live_capture_uses_connected_source_session(monkeypatch, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    state_path = project_root / "sources" / "sessions" / "eursap.storage-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"cookies": []}', encoding="utf-8")
    SourceSessionService(project_root).record_storage_state(
        "eursap-jobs",
        session_scope="eursap.eu",
        storage_state_path="sources/sessions/eursap.storage-state.json",
    )
    captured: dict[str, object] = {}

    def fake_capture(*args, **kwargs):
        captured["session_state_path"] = kwargs.get("session_state_path")
        captured["source_session_scope"] = kwargs.get("source_session_scope")
        return SimpleNamespace(
            artifact_dir=artifact,
            warnings=[],
            candidate_count=1,
            recipe_extracted_count=1,
            detail_sample_url="",
        )

    def fake_refinement(*args, **kwargs):
        result = RecipeSuggestionResult(
            source_name="Eursap Jobs",
            start_url="https://eursap.eu/jobs",
            artifact_dir=artifact,
            suggested_recipe_yaml=VALID_RECIPE_YAML,
            schema_valid=True,
            selected_strategy="selector_based",
            confidence="high",
        )
        attempt = RecipeRefinementAttempt(
            attempt_number=1,
            suggested_recipe_yaml=VALID_RECIPE_YAML,
            schema_valid=True,
            validation_errors=[],
            quality_status="good",
            quality_warnings=[],
            extracted_job_count=1,
            useful_titles=1,
            unique_urls=1,
        )
        return RecipeRefinementResult(final_result=result, attempts=[attempt], accepted=True)

    monkeypatch.setattr("job_agent.services.recipe_generation_run_service.capture_recipe_calibration", fake_capture)
    monkeypatch.setattr(
        "job_agent.services.recipe_generation_run_service.suggest_recipe_with_refinement", fake_refinement
    )

    run = RecipeGenerationRunService(project_root).start_from_source_capture(
        "eursap-jobs",
        run_async=False,
    )

    assert captured["session_state_path"] == state_path
    assert captured["source_session_scope"] == "eursap.eu"
    assert run["source_session_used"] is True
    assert run["source_session_scope"] == "eursap.eu"


def _write_artifact(project_root: Path) -> Path:
    artifact = project_root / "output" / "recipe-calibration" / "eursap"
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "summary.md").write_text("# Eursap\n", encoding="utf-8")
    (artifact / "visible-text.txt").write_text("SAP Basis Consultant Remote Contract", encoding="utf-8")
    (artifact / "candidate-elements.html").write_text(
        '<article class="job-card"><a class="job-link" href="/jobs/sap-basis">SAP Basis Consultant</a></article>',
        encoding="utf-8",
    )
    (artifact / "page.html").write_text(
        """
        <article class="job-card">
          <a class="job-link" href="/jobs/sap-basis">SAP Basis Consultant</a>
          <p class="description">SAP Basis contract role supporting an S/4HANA migration with operations work.</p>
        </article>
        """,
        encoding="utf-8",
    )
    (artifact / "selector-report.json").write_text(
        json.dumps(
            {
                "url": "https://eursap.eu/jobs",
                "capture_mode": "static_html",
                "candidates": [{"selector": "article.job-card", "kind": "card"}],
            }
        ),
        encoding="utf-8",
    )
    return artifact


def _suggestion_result(artifact: Path, source_name: str = "Eursap Jobs"):
    from job_agent.services.recipe_suggestion_service import RecipeSuggestionResult

    return RecipeSuggestionResult(
        source_name=source_name,
        start_url="https://eursap.eu/jobs",
        artifact_dir=artifact,
        suggested_recipe_yaml=VALID_RECIPE_YAML,
        schema_valid=True,
        selected_strategy="selector_based",
        confidence="high",
    )


def _unusable_suggestion_result(artifact: Path):
    from job_agent.services.recipe_suggestion_service import RecipeSuggestionResult

    return RecipeSuggestionResult(
        source_name="Eursap Jobs",
        start_url="https://eursap.eu/jobs",
        artifact_dir=artifact,
        suggested_recipe_yaml="",
        schema_valid=False,
        validation_errors=["No stable repeated listing card selector was found."],
        selected_strategy="not_recommended",
        confidence="low",
    )


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""
