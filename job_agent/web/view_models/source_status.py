from __future__ import annotations

from typing import Any


def build_source_page_status(
    source,
    execution_entry,
    readiness,
    *,
    recipe_preview_url: str,
    generation_status=None,
) -> dict[str, Any]:
    execution_enabled = bool(execution_entry and execution_entry.get("enabled", True))
    latest_reviewable_candidate_id = (
        getattr(generation_status, "latest_reviewable_candidate_id", "") if generation_status else ""
    )
    reviewable_pending_candidates = (
        int(getattr(generation_status, "reviewable_pending_candidates", 0) or 0) if generation_status else 0
    )
    latest_approved_candidate_id = getattr(generation_status, "latest_approved_candidate_id", "") if generation_status else ""
    approved_recipe_path = getattr(generation_status, "latest_approved_recipe_path", "") if generation_status else ""
    approved_matches_source = bool(
        getattr(generation_status, "approved_matches_source_recipe_path", False)
    ) if generation_status else False
    status = {
        "title": "Ready for review",
        "summary": "This source is saved, but it still needs a reading plan review and source test before the daily run can use it.",
        "badge": "In setup",
        "badge_class": "medium",
        "primary_action": None,
        "secondary_action": {"type": "link", "label": "Edit settings", "href": "#source-settings"},
        "preview_label": _status_label(source.health.health_status),
        "source_test_label": _status_label(readiness.readiness_status),
        "automation_label": "Included" if execution_enabled else "Prepared but off" if execution_entry else "Not included",
        "blockers": [],
    }
    if source.status == "archived":
        status.update(
            {
                "title": "Archived",
                "summary": "This source is hidden from normal source lists and cannot run automatically until restored.",
                "badge": "Archived",
                "badge_class": "low",
                "primary_action": {
                    "type": "post",
                    "label": "Restore source",
                    "action": f"/sources/{source.id}/restore",
                },
                "blockers": ["Restore this source before testing or enabling it."],
            }
        )
    elif source.kind == "manual":
        status.update(
            {
                "title": "Manual intake",
                "summary": "Use this for recruiter emails, copied postings, and sources that should not be automated.",
                "badge": "Manual",
                "badge_class": "high",
                "preview_label": "Not needed",
                "source_test_label": "Not needed",
                "automation_label": "Manual only",
            }
        )
    elif source.kind == "local_yaml":
        status.update(
            {
                "title": "Local sample source",
                "summary": "Jobs come from a local YAML file for samples or controlled imports.",
                "badge": "Local file",
                "badge_class": "high",
                "preview_label": "Not needed",
                "source_test_label": "Not needed",
                "automation_label": "Configured locally",
            }
        )
    elif not source.url:
        status.update(
            {
                "title": "Needs a source URL",
                "summary": "Add the job-board URL before testing extraction.",
                "badge": "Needs setup",
                "badge_class": "medium",
                "primary_action": {"type": "link", "label": "Add source URL", "href": "#source-settings"},
                "blockers": ["No source URL is saved."],
            }
        )
    elif not source.recipe_path:
        if reviewable_pending_candidates and latest_reviewable_candidate_id:
            status.update(
                {
                    "title": "Review generated reading plan",
                    "summary": "A generated reading plan is waiting for you to review and select.",
                    "badge": "Review plan",
                    "badge_class": "medium",
                    "primary_action": {
                        "type": "link",
                        "label": "Review reading plan",
                        "href": f"/recipe-candidates/{latest_reviewable_candidate_id}?source_id={source.id}",
                    },
                    "blockers": ["No reading plan is selected yet."],
                }
            )
            return status
        if approved_recipe_path and latest_approved_candidate_id and not approved_matches_source:
            status.update(
                {
                    "title": "Use saved reading plan",
                    "summary": "A reviewed reading plan exists, but this source is not using it yet.",
                    "badge": "Select plan",
                    "badge_class": "medium",
                    "primary_action": {
                        "type": "link",
                        "label": "Use reading plan",
                        "href": f"/recipe-candidates/{latest_approved_candidate_id}?source_id={source.id}",
                    },
                    "blockers": ["No reading plan is selected yet."],
                }
            )
            return status
        status.update(
            {
                "title": "Teach the app how to read this source",
                "summary": "The app needs a reading plan before it can find jobs, follow pagination, or open job details.",
                "badge": "Needs setup",
                "badge_class": "medium",
                "primary_action": {
                    "type": "post",
                    "label": "Learn source",
                    "action": f"/sources/{source.id}/reading-plan/learn",
                },
                "blockers": ["No reading plan is selected."],
            }
        )
    elif source.health.health_status != "good":
        status.update(
            {
                "title": "Not ready yet",
                "summary": "The reading plan has not passed a saved review. Review what it reads before testing the whole source.",
                "badge": "Needs review",
                "badge_class": "medium",
                "primary_action": {"type": "link", "label": "Review what it reads", "href": recipe_preview_url},
                "blockers": [source.health.health_summary],
            }
        )
    elif not execution_entry:
        status.update(
            {
                "title": "Ready for a safe source test",
                "summary": "The reading plan review passed. Test the source end-to-end without saving jobs.",
                "badge": "Review passed",
                "badge_class": "high",
                "primary_action": {
                    "type": "link",
                    "label": "Test source safely",
                    "href": f"/sources/{source.id}/test-run",
                },
            }
        )
    elif readiness.readiness_status != "ready":
        status.update(
            {
                "title": "Needs a source test",
                "summary": "Review passed. Now test this source the way the daily run would use it, without saving jobs.",
                "badge": "Needs source test",
                "badge_class": "medium",
                "primary_action": {
                    "type": "link",
                    "label": "Test source safely",
                    "href": f"/sources/{source.id}/test-run",
                },
                "blockers": list(readiness.blockers[:3]),
            }
        )
    elif not execution_enabled:
        status.update(
            {
                "title": "Ready to enable",
                "summary": "Recipe review and source test passed. This source is still excluded from the daily run until you include it.",
                "badge": "Ready",
                "badge_class": "high",
                "primary_action": {
                    "type": "post",
                    "label": "Include in daily run",
                    "action": f"/sources/{source.id}/enable-when-ready",
                },
            }
        )
    else:
        status.update(
            {
                "title": "Included in daily run",
                "summary": "This source is enabled for the daily run.",
                "badge": "Enabled",
                "badge_class": "high",
                "primary_action": {
                    "type": "post",
                    "label": "Run this source now",
                    "action": f"/sources/{source.id}/run-now",
                },
            }
        )
    return status


def build_source_setup_steps(
    source,
    execution_entry,
    readiness,
    generation_status,
    *,
    recipe_preview_url: str,
) -> list[dict[str, object]]:
    if source.kind == "manual":
        return [
            {
                "title": "Manual intake",
                "summary": "This source is for jobs you add by hand from emails, recruiters, or copied postings.",
                "badge": "Ready",
                "badge_class": "high",
                "state": "complete",
                "action": None,
            }
        ]
    if source.kind == "local_yaml":
        return [
            {
                "title": "Local source",
                "summary": "This source reads controlled local YAML data rather than a public job-board page.",
                "badge": "Ready",
                "badge_class": "high",
                "state": "complete",
                "action": None,
            }
        ]
    latest_reviewable_candidate_id = (
        getattr(generation_status, "latest_reviewable_candidate_id", "") if generation_status else ""
    )
    reviewable_pending_candidates = (
        int(getattr(generation_status, "reviewable_pending_candidates", 0) or 0) if generation_status else 0
    )
    execution_enabled = bool(execution_entry and execution_entry.get("enabled", True))
    steps = [
        {
            "title": "Add source",
            "summary": "The job-board URL is saved for review.",
            "badge": "Done" if source.url else "Needs URL",
            "badge_class": "high" if source.url else "medium",
            "state": "complete" if source.url else "todo",
            "action": {"type": "link", "label": "Edit URL", "href": "#source-settings"} if not source.url else None,
        }
    ]
    if source.recipe_path:
        learn_badge = "Selected"
        learn_state = "complete"
        learn_action = None
        learn_summary = "A reading plan is selected for this source."
    elif reviewable_pending_candidates and latest_reviewable_candidate_id:
        learn_badge = "Review"
        learn_state = "active"
        learn_action = {
            "type": "link",
            "label": "Review plan",
            "href": f"/recipe-candidates/{latest_reviewable_candidate_id}?source_id={source.id}",
        }
        learn_summary = "A generated reading plan is waiting for review."
    else:
        learn_badge = "Next"
        learn_state = "active" if source.url else "blocked"
        learn_action = (
            {"type": "post", "label": "Learn source", "action": f"/sources/{source.id}/reading-plan/learn"}
            if source.url and source.status != "archived"
            else None
        )
        learn_summary = "Teach the app where job cards, pagination, detail pages, and fields are."
    steps.append(
        {
            "title": "Learn source",
            "summary": learn_summary,
            "badge": learn_badge,
            "badge_class": "high" if learn_state == "complete" else "medium",
            "state": learn_state,
            "action": learn_action,
        }
    )
    review_ready = source.recipe_path and source.health.health_status == "good"
    steps.append(
        {
            "title": "Review what it reads",
            "summary": source.health.health_summary if source.recipe_path else "Select a reading plan before reviewing extraction.",
            "badge": "Passed" if review_ready else ("Run review" if source.recipe_path else "Waiting"),
            "badge_class": "high" if review_ready else "medium",
            "state": "complete" if review_ready else ("active" if source.recipe_path else "blocked"),
            "action": (
                {"type": "link", "label": "Review extraction", "href": recipe_preview_url}
                if source.recipe_path and not review_ready
                else None
            ),
        }
    )
    test_ready = readiness.readiness_status == "ready"
    steps.append(
        {
            "title": "Test safely",
            "summary": readiness.readiness_summary if source.recipe_path else "A source test runs the plan without saving jobs.",
            "badge": "Passed" if test_ready else ("Run test" if review_ready else "Waiting"),
            "badge_class": "high" if test_ready else "medium",
            "state": "complete" if test_ready else ("active" if review_ready else "blocked"),
            "action": (
                {"type": "link", "label": "Test source", "href": f"/sources/{source.id}/test-run"}
                if review_ready and not test_ready
                else None
            ),
        }
    )
    steps.append(
        {
            "title": "Include in daily run",
            "summary": (
                "This source is included in automatic job checks."
                if execution_enabled
                else "Keep it off until the review and source test both pass."
            ),
            "badge": "Included" if execution_enabled else ("Ready" if test_ready else "Off"),
            "badge_class": "high" if execution_enabled or test_ready else "medium",
            "state": "complete" if execution_enabled else ("active" if test_ready else "blocked"),
            "action": (
                {"type": "post", "label": "Include in daily run", "action": f"/sources/{source.id}/enable-when-ready"}
                if test_ready and not execution_enabled
                else None
            ),
        }
    )
    return steps


def _status_label(status: str) -> str:
    return {
        "good": "Passed",
        "warning": "Needs review",
        "failing": "Failed",
        "untested": "Not tested",
        "ready": "Ready",
        "blocked": "Blocked",
        "no_data": "No data",
    }.get(str(status or "").strip(), str(status or "Unknown").replace("_", " ").title())
