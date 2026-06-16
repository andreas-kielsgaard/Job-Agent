from __future__ import annotations

from typing import Any

from job_agent.services.recipe_candidate_policy import (
    candidate_has_quality_blockers,
    candidate_has_testable_recipe,
    candidate_is_reviewable,
)
from job_agent.services.recipe_candidate_service import RecipeCandidate


def build_candidate_reading_plan_review(
    candidate: RecipeCandidate,
    source: Any | None,
    approval_recipe_path: str,
) -> dict[str, Any]:
    can_use_generated = candidate_is_reviewable(candidate)
    can_test_generated = candidate_has_testable_recipe(candidate)
    has_quality_blockers = candidate_has_quality_blockers(candidate)
    source_id = _source_id(source)
    source_has_selected_recipe = _source_has_selected_recipe(source)
    status = _candidate_status(
        candidate,
        can_use_generated=can_use_generated,
        has_quality_blockers=has_quality_blockers,
        source_has_selected_recipe=source_has_selected_recipe,
    )
    actions: list[dict[str, Any]] = []

    if can_use_generated:
        actions.append(_approve_action(candidate, source_id, approval_recipe_path, allow_quality_warnings=False))
    elif has_quality_blockers and source_id:
        actions.append(_approve_action(candidate, source_id, approval_recipe_path, allow_quality_warnings=True))

    if candidate.status == "approved" and source_id and not candidate.adopted_at:
        actions.append(_adopt_action(candidate, source_id))

    if source_has_selected_recipe:
        label = (
            "Run source test"
            if candidate.status == "approved" and bool(candidate.adopted_at)
            else "Run source test for selected plan"
        )
        button_class = "button" if label == "Run source test" else "button light"
        actions.append(_source_test_action(source_id, label=label, button_class=button_class))

    if candidate.status == "pending":
        actions.append(_discard_action(candidate, source_id))
        if source_id and not can_use_generated:
            actions.append(_learn_again_action(source_id))

    return {
        **status,
        "actions": actions,
        "can_use_generated_plan": can_use_generated,
        "can_test_generated_plan": can_test_generated,
        "has_quality_blockers": has_quality_blockers,
        "source_has_selected_recipe": source_has_selected_recipe,
    }


def build_generation_run_reading_plan_review(run: dict[str, Any], source: Any | None) -> dict[str, Any]:
    has_yaml = bool(str(run.get("suggested_recipe_yaml") or "").strip())
    schema_valid = bool(run.get("schema_valid"))
    clearly_failed = _generation_clearly_failed(run, has_yaml=has_yaml, schema_valid=schema_valid)
    has_quality_blockers = bool(
        schema_valid
        and has_yaml
        and not clearly_failed
        and (str(run.get("quality_status") or "") == "poor" or (run.get("refinement_used") and not run.get("refinement_accepted")))
    )
    can_use_generated = bool(schema_valid and has_yaml and not has_quality_blockers and not clearly_failed)
    can_test_generated = bool(schema_valid and has_yaml and not clearly_failed)
    source_id = _source_id(source)
    source_has_selected_recipe = _source_has_selected_recipe(source)
    actions: list[dict[str, Any]] = []

    if can_test_generated and run.get("candidate_approval_url") and run.get("approval_recipe_path"):
        actions.append(
            _generated_run_approve_action(
                run,
                source_id,
                allow_quality_warnings=has_quality_blockers,
            )
        )
    if source_has_selected_recipe and not clearly_failed:
        actions.append(_source_test_action(source_id, label="Run source test for selected plan", button_class="button light"))
    if run.get("compatibility_url"):
        actions.append(_link_action(str(run["compatibility_url"]), "Compatibility evidence", button_class="button light"))
    if source_id:
        actions.append(_link_action(f"/sources/{source_id}", "Back to source", button_class="button light"))

    return {
        **_generation_status(
            can_use_generated=can_use_generated,
            can_test_generated=can_test_generated,
            has_quality_blockers=has_quality_blockers,
            clearly_failed=clearly_failed,
            source_has_selected_recipe=source_has_selected_recipe,
        ),
        "actions": actions,
        "can_use_generated_plan": can_use_generated,
        "can_test_generated_plan": can_test_generated,
        "has_quality_blockers": has_quality_blockers,
        "clearly_failed": clearly_failed,
        "source_has_selected_recipe": source_has_selected_recipe,
    }


def _candidate_status(
    candidate: RecipeCandidate,
    *,
    can_use_generated: bool,
    has_quality_blockers: bool,
    source_has_selected_recipe: bool,
) -> dict[str, str]:
    if can_use_generated:
        return {
            "badge_label": "Ready to test",
            "badge_class": "high",
            "title": "Use this plan, then verify it with a source test",
            "summary": (
                "The local extraction count is only a calibration sanity check. The safe source test is what "
                "verifies live pagination, source access, and detail reads before indexing or daily runs."
            ),
        }
    if has_quality_blockers:
        return {
            "badge_label": "Test with warning",
            "badge_class": "medium",
            "title": "Local checks found issues, but the plan can be source-tested",
            "summary": (
                "The generated YAML is schema-valid, but local calibration did not pass. Proceeding will select "
                "this plan only for the safe source test; the source test may fail and daily-run enablement remains "
                "blocked until readiness passes."
            ),
        }
    if candidate.status == "pending" and source_has_selected_recipe:
        return {
            "badge_label": "Draft blocked",
            "badge_class": "medium",
            "title": "This generated attempt is not selectable",
            "summary": (
                "This saved attempt did not produce selectable rules. The source already has a selected reading "
                "plan, so you can run the safe source test against that plan or learn the source again."
            ),
        }
    if candidate.status == "pending":
        return {
            "badge_label": "Needs better sample",
            "badge_class": "medium",
            "title": "This generated attempt is not selectable",
            "summary": (
                "This saved attempt did not produce selectable rules. Use it as evidence for what went wrong, "
                "then learn the source again with a better capture."
            ),
        }
    if candidate.status == "approved":
        return {
            "badge_label": "Approved",
            "badge_class": "high",
            "title": "Reading plan saved",
            "summary": (
                "This plan is not included in the daily run by itself. It must be selected for the source and "
                "tested safely first."
            ),
        }
    if candidate.status == "rejected":
        return {
            "badge_label": "Rejected",
            "badge_class": "medium",
            "title": "Reading plan discarded",
            "summary": (
                "This plan is not included in the daily run by itself. It must be selected for the source and "
                "tested safely first."
            ),
        }
    return {
        "badge_label": candidate.status.replace("_", " ").title(),
        "badge_class": "medium",
        "title": f"Reading plan {candidate.status}",
        "summary": (
            "This plan is not included in the daily run by itself. It must be selected for the source and tested "
            "safely first."
        ),
    }


def _generation_status(
    *,
    can_use_generated: bool,
    can_test_generated: bool,
    has_quality_blockers: bool,
    clearly_failed: bool,
    source_has_selected_recipe: bool,
) -> dict[str, str]:
    if clearly_failed:
        return {
            "badge_label": "Failed",
            "badge_class": "low",
            "title": "Generation failed to produce a testable plan",
            "summary": (
                "No schema-valid reading plan was generated, so this result cannot proceed to source testing. "
                "Use the notes below as diagnostic evidence, then learn the source again when new evidence is available."
            ),
        }
    if can_use_generated:
        return {
            "badge_label": "Schema valid",
            "badge_class": "high",
            "title": "Generated Reading Plan",
            "summary": "Use the generated plan, then let the safe source test verify pagination, access, and details.",
        }
    if has_quality_blockers:
        return {
            "badge_label": "Test with warning",
            "badge_class": "medium",
            "title": "Generated Reading Plan",
            "summary": (
                "The generated YAML is schema-valid, but local calibration did not pass. You can still proceed "
                "to the safe source test, which remains the live-access gate."
            ),
        }
    if not can_test_generated and source_has_selected_recipe:
        return {
            "badge_label": "Not testable",
            "badge_class": "medium",
            "title": "Generated attempt is not testable",
            "summary": "This generated attempt is not selectable. Run a new learning pass before source testing it.",
        }
    return {
        "badge_label": "Schema invalid",
        "badge_class": "medium",
        "title": "Generated attempt is not testable",
        "summary": "The generator saved evidence, but it did not produce schema-valid YAML that can be tested.",
    }


def _generation_clearly_failed(run: dict[str, Any], *, has_yaml: bool, schema_valid: bool) -> bool:
    if str(run.get("status") or "") == "failed":
        return True
    if not has_yaml or not schema_valid:
        return True
    return str(run.get("selected_strategy") or "") == "not_recommended"


def _approve_action(
    candidate: RecipeCandidate,
    source_id: str,
    recipe_path: str,
    *,
    allow_quality_warnings: bool,
) -> dict[str, Any]:
    fields = [{"name": "recipe_path", "value": recipe_path}]
    if source_id:
        fields = [
            {"name": "source_id", "value": source_id},
            *fields,
            {"name": "overwrite", "value": "1"},
            {"name": "next_action", "value": "test"},
        ]
    if allow_quality_warnings:
        fields.append({"name": "allow_quality_warnings", "value": "1"})
    return _form_action(
        f"/recipe-candidates/{candidate.candidate_id}/approve",
        "Proceed with source test"
        if allow_quality_warnings
        else ("Use plan and run source test" if source_id else "Use this reading plan"),
        fields,
    )


def _generated_run_approve_action(
    run: dict[str, Any],
    source_id: str,
    *,
    allow_quality_warnings: bool,
) -> dict[str, Any]:
    fields = [
        {"name": "source_id", "value": source_id},
        {"name": "recipe_path", "value": str(run.get("approval_recipe_path") or "")},
        {"name": "overwrite", "value": "1"},
        {"name": "next_action", "value": "test"},
    ]
    if allow_quality_warnings:
        fields.append({"name": "allow_quality_warnings", "value": "1"})
    return _form_action(
        str(run["candidate_approval_url"]),
        "Proceed with source test" if allow_quality_warnings else "Use plan and run source test",
        fields,
    )


def _adopt_action(candidate: RecipeCandidate, source_id: str) -> dict[str, Any]:
    return _form_action(
        f"/recipe-candidates/{candidate.candidate_id}/adopt",
        "Use for this source and test",
        [
            {"name": "source_id", "value": source_id},
            {"name": "next_action", "value": "test"},
        ],
    )


def _discard_action(candidate: RecipeCandidate, source_id: str) -> dict[str, Any]:
    fields = [{"name": "reason", "value": "Discarded from generated reading-plan result."}]
    if source_id:
        fields.insert(0, {"name": "source_id", "value": source_id})
    return _form_action(
        f"/recipe-candidates/{candidate.candidate_id}/reject",
        "Discard",
        fields,
        button_class="button light",
    )


def _learn_again_action(source_id: str) -> dict[str, Any]:
    return _form_action(f"/sources/{source_id}/reading-plan/learn", "Learn source again", [], button_class="button")


def _source_test_action(source_id: str, *, label: str, button_class: str) -> dict[str, Any]:
    return _link_action(f"/sources/{source_id}/test-run?start=1", label, button_class=button_class)


def _form_action(
    action: str,
    label: str,
    fields: list[dict[str, str]],
    *,
    button_class: str = "button",
) -> dict[str, Any]:
    return {
        "type": "form",
        "action": action,
        "method": "post",
        "label": label,
        "button_class": button_class,
        "fields": fields,
    }


def _link_action(href: str, label: str, *, button_class: str) -> dict[str, Any]:
    return {"type": "link", "href": href, "label": label, "button_class": button_class}


def _source_id(source: Any | None) -> str:
    return str(getattr(source, "id", "") or "").strip() if source else ""


def _source_has_selected_recipe(source: Any | None) -> bool:
    return bool(source and str(getattr(source, "recipe_path", "") or "").strip())
