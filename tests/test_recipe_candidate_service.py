from __future__ import annotations

from pathlib import Path

import yaml

from job_agent.cli import list_recipe_candidates, reject_recipe_candidate, show_recipe_candidate, suggest_recipe
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_suggestion_service import (
    RecipeRefinementAttempt,
    RecipeRefinementResult,
    RecipeSuggestionResult,
)


VALID_RECIPE_YAML = """source_name: Example Jobs
start_url: https://example.com/jobs
mode: static_html
listing:
  card_selector: article.job-card
  title_selector: a.job-link
  link_selector: a.job-link
limits:
  max_cards: 10
"""


def test_saving_non_refined_suggestion_creates_pending_candidate(project_root: Path) -> None:
    result = _suggestion(project_root)

    candidate = RecipeCandidateStore(project_root).save_candidate_from_suggestion(result)

    path = project_root / "output" / "recipe-candidates" / f"{candidate.candidate_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert candidate.status == "pending"
    assert data["suggested_recipe_yaml"].startswith("source_name: Example Jobs")
    assert data["schema_valid"] is True
    assert data["refinement_used"] is False


def test_saving_refined_result_preserves_attempt_history_and_quality(project_root: Path) -> None:
    refinement = _refinement(project_root)

    candidate = RecipeCandidateStore(project_root).save_candidate_from_refinement(refinement)

    assert candidate.refinement_used is True
    assert candidate.refinement_accepted is True
    assert candidate.attempt_count == 2
    assert candidate.quality_status == "good"
    assert candidate.extracted_job_count == 3
    assert candidate.attempts[0]["quality_status"] == "poor"


def test_candidate_ids_are_unique_and_files_are_not_overwritten(project_root: Path) -> None:
    store = RecipeCandidateStore(project_root)

    first = store.save_candidate_from_suggestion(_suggestion(project_root))
    second = store.save_candidate_from_suggestion(_suggestion(project_root))

    assert first.candidate_id != second.candidate_id
    assert store.candidate_path(first.candidate_id).exists()
    assert store.candidate_path(second.candidate_id).exists()


def test_listing_candidates_returns_summaries_and_filters_status(project_root: Path) -> None:
    store = RecipeCandidateStore(project_root)
    pending = store.save_candidate_from_suggestion(_suggestion(project_root))
    rejected = store.save_candidate_from_suggestion(_suggestion(project_root, source_name="Rejected Jobs"))
    store.reject_candidate(rejected.candidate_id, reason="Noisy")

    all_candidates = store.list_candidates()
    pending_only = store.list_candidates(status="pending")
    rejected_only = store.list_candidates(status="rejected")

    assert {candidate.candidate_id for candidate in all_candidates} == {pending.candidate_id, rejected.candidate_id}
    assert [candidate.candidate_id for candidate in pending_only] == [pending.candidate_id]
    assert [candidate.candidate_id for candidate in rejected_only] == [rejected.candidate_id]


def test_loading_candidate_returns_full_yaml_and_metadata(project_root: Path) -> None:
    store = RecipeCandidateStore(project_root)
    saved = store.save_candidate_from_suggestion(_suggestion(project_root))

    loaded = store.load_candidate(saved.candidate_id)

    assert loaded.candidate_id == saved.candidate_id
    assert loaded.suggested_recipe_yaml == VALID_RECIPE_YAML
    assert loaded.evidence_summary == "candidate selectors: article.job-card"


def test_rejecting_candidate_updates_status_reason_and_timestamp(project_root: Path) -> None:
    store = RecipeCandidateStore(project_root)
    saved = store.save_candidate_from_suggestion(_suggestion(project_root))

    rejected = store.reject_candidate(saved.candidate_id, reason="Wrong cards")

    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "Wrong cards"
    assert rejected.rejected_at
    assert store.load_candidate(saved.candidate_id).status == "rejected"


def test_cli_suggest_recipe_save_candidate_writes_review_object_only(monkeypatch, capsys, project_root: Path) -> None:
    artifact = _artifact(project_root)

    monkeypatch.setattr(
        "job_agent.services.recipe_suggestion_service.suggest_recipe_from_artifact",
        lambda *args, **kwargs: _suggestion(project_root),
    )

    suggest_recipe(str(artifact), save_candidate=True, root=project_root)

    output = capsys.readouterr().out
    assert "Recipe candidate saved:" in output
    assert list((project_root / "output" / "recipe-candidates").glob("*.yaml"))
    assert not (project_root / "sources" / "recipes" / "generated.yaml").exists()


def test_cli_candidate_list_show_and_reject_use_temp_root(capsys, project_root: Path) -> None:
    store = RecipeCandidateStore(project_root)
    saved = store.save_candidate_from_refinement(_refinement(project_root))

    list_recipe_candidates(root=project_root)
    list_output = capsys.readouterr().out
    assert saved.candidate_id in list_output
    assert "quality=good" in list_output

    show_recipe_candidate(saved.candidate_id, root=project_root)
    show_output = capsys.readouterr().out
    assert "Suggested recipe YAML:" in show_output
    assert "Attempt 1" in show_output

    reject_recipe_candidate(saved.candidate_id, reason="Hold for later", root=project_root)
    reject_output = capsys.readouterr().out
    assert "Recipe candidate rejected" in reject_output
    assert store.load_candidate(saved.candidate_id).status == "rejected"


def _suggestion(project_root: Path, source_name: str = "Example Jobs") -> RecipeSuggestionResult:
    return RecipeSuggestionResult(
        source_name=source_name,
        start_url="https://example.com/jobs",
        artifact_dir=_artifact(project_root),
        suggested_recipe_yaml=VALID_RECIPE_YAML,
        explanation="Use job cards.",
        confidence="high",
        assumptions=["Cards are repeated."],
        warnings=["Review before promotion."],
        evidence_summary="candidate selectors: article.job-card",
        selected_strategy="selector_based",
        referenced_artifact_files=["summary.md", "selector-report.json", "page.html"],
        schema_valid=True,
    )


def _refinement(project_root: Path) -> RecipeRefinementResult:
    return RecipeRefinementResult(
        final_result=_suggestion(project_root),
        attempts=[
            RecipeRefinementAttempt(
                attempt_number=1,
                suggested_recipe_yaml=VALID_RECIPE_YAML.replace("article.job-card", "article.missing"),
                schema_valid=True,
                validation_errors=[],
                quality_status="poor",
                quality_warnings=["No jobs were extracted from local page.html."],
                revision_reason="Recipe extracted no jobs from local page.html.",
            ),
            RecipeRefinementAttempt(
                attempt_number=2,
                suggested_recipe_yaml=VALID_RECIPE_YAML,
                schema_valid=True,
                validation_errors=[],
                quality_status="good",
                quality_warnings=[],
                extracted_job_count=3,
                useful_titles=3,
                generic_labels=0,
                unique_urls=3,
                average_description_length=120,
            ),
        ],
        accepted=True,
    )


def _artifact(project_root: Path) -> Path:
    artifact = project_root / "output" / "recipe-calibration" / "example"
    artifact.mkdir(parents=True, exist_ok=True)
    return artifact
