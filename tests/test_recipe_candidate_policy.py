from __future__ import annotations

from job_agent.services.recipe_candidate_policy import (
    candidate_has_quality_blockers,
    candidate_has_testable_recipe,
    candidate_is_reviewable,
)
from job_agent.services.recipe_candidate_service import RecipeCandidate


def test_reviewable_candidate_is_testable_without_quality_blockers() -> None:
    candidate = _candidate()

    assert candidate_is_reviewable(candidate) is True
    assert candidate_has_testable_recipe(candidate) is True
    assert candidate_has_quality_blockers(candidate) is False


def test_schema_valid_poor_candidate_is_testable_but_not_reviewable() -> None:
    candidate = _candidate(quality_status="poor")

    assert candidate_is_reviewable(candidate) is False
    assert candidate_has_testable_recipe(candidate) is True
    assert candidate_has_quality_blockers(candidate) is True


def test_invalid_or_empty_candidate_is_not_testable() -> None:
    assert candidate_has_testable_recipe(_candidate(schema_valid=False)) is False
    assert candidate_has_testable_recipe(_candidate(suggested_recipe_yaml="")) is False


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
