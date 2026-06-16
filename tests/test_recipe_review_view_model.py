from __future__ import annotations

from types import SimpleNamespace

from job_agent.services.recipe_candidate_service import RecipeCandidate
from job_agent.web.view_models.recipe_review import build_candidate_reading_plan_review


def test_invalid_candidate_with_selected_source_plan_offers_selected_plan_test() -> None:
    review = build_candidate_reading_plan_review(
        _candidate(schema_valid=False, suggested_recipe_yaml=""),
        SimpleNamespace(id="example-source", recipe_path="sources/recipes/experimental/example.yaml"),
        "sources/recipes/experimental/example.yaml",
    )

    labels = [action["label"] for action in review["actions"]]

    assert review["title"] == "This generated attempt is not selectable"
    assert review["source_has_selected_recipe"] is True
    assert "Run source test for selected plan" in labels
    assert "Use plan and run source test" not in labels
    assert "Proceed with source test" not in labels


def _candidate(**overrides) -> RecipeCandidate:
    values = {
        "candidate_id": "candidate-1",
        "status": "pending",
        "created_at": "2026-06-12T00:00:00+00:00",
        "updated_at": "2026-06-12T00:00:00+00:00",
        "source_name": "Example",
        "start_url": "https://example.com/jobs",
        "artifact_dir": "output/recipe-calibration/example",
        "suggested_recipe_yaml": "source_name: Example\nlisting:\n  card_selector: article\n",
        "schema_valid": True,
        "quality_status": "good",
    }
    values.update(overrides)
    return RecipeCandidate(**values)
