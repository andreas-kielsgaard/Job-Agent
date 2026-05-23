from __future__ import annotations

from job_agent.services.recipe_candidate_service import RecipeCandidate


def candidate_is_reviewable(candidate: RecipeCandidate) -> bool:
    return (
        candidate.status == "pending"
        and bool(candidate.suggested_recipe_yaml.strip())
        and bool(candidate.schema_valid)
        and candidate.quality_status != "poor"
        and (not candidate.refinement_used or candidate.refinement_accepted)
    )
