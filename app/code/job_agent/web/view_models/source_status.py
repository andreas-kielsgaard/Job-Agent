from __future__ import annotations

from typing import Any


def build_source_run_eligibility(
    source,
    execution_entry,
    readiness,
    *,
    index_status: dict[str, object] | None = None,
) -> dict[str, Any]:
    index_status = index_status or {}
    source_kind = str(getattr(source, "kind", "") or "")
    source_status = str(getattr(source, "status", "") or "")
    archived = source_status == "archived"
    local_or_manual = source_kind in {"manual", "local_yaml"}
    configured = bool(execution_entry)
    enabled = bool(execution_entry and execution_entry.get("enabled", True))
    readiness_ready = bool(local_or_manual or getattr(readiness, "readiness_status", "") == "ready")
    index_complete = bool(local_or_manual or index_status.get("complete"))
    stale_recipe_source_test = readiness_has_stale_recipe_test(readiness)
    eligible = bool(enabled and readiness_ready and index_complete and not archived)
    blockers: list[str] = []
    if archived:
        blockers.append("Archived sources are skipped.")
    if not configured:
        blockers.append("No daily-run execution entry is configured.")
    elif not enabled:
        blockers.append("Daily-run execution is turned off.")
    if not readiness_ready and not local_or_manual:
        blockers.extend(_readiness_blockers(readiness))
    if readiness_ready and not index_complete:
        blockers.append("Listing index is missing or stale.")
    if eligible:
        label = "Will run"
        badge_class = "high"
        title = "Eligible now"
        summary = "Enabled, source-test proof is current, and the listing index is ready."
    elif enabled:
        label = "Will be skipped"
        badge_class = "warning"
        title = "Configured but blocked"
        summary = "Enabled in the daily-run config, but current checks would skip it."
    elif configured:
        label = "Configured off"
        badge_class = "medium"
        title = "Configured but off"
        summary = "A daily-run entry exists, but it is turned off."
    else:
        label = "Not configured"
        badge_class = "waiting"
        title = "Not in daily run"
        summary = "No daily-run execution entry exists for this source."
    return {
        "eligible": eligible,
        "configured": configured,
        "enabled": enabled,
        "readiness_ready": readiness_ready,
        "index_complete": index_complete,
        "stale_recipe_source_test": stale_recipe_source_test,
        "label": label,
        "badge_class": badge_class,
        "title": title,
        "summary": summary,
        "blockers": blockers[:4],
    }


def readiness_has_stale_recipe_test(readiness) -> bool:
    checks = getattr(readiness, "checks", {}) or {}
    if isinstance(checks, dict) and checks.get("recipe_changed_after_source_test"):
        return True
    blockers = [str(item).lower() for item in getattr(readiness, "blockers", []) or []]
    return any("reading plan changed since the saved source test" in blocker for blocker in blockers)


def build_source_page_status(
    source,
    execution_entry,
    readiness,
    *,
    generation_status=None,
    session_status=None,
    index_status: dict[str, object] | None = None,
) -> dict[str, Any]:
    execution_enabled = bool(execution_entry and execution_entry.get("enabled", True))
    index_status = index_status or {}
    index_complete = bool(index_status.get("complete"))
    latest_reviewable_candidate_id = (
        getattr(generation_status, "latest_reviewable_candidate_id", "") if generation_status else ""
    )
    reviewable_pending_candidates = (
        int(getattr(generation_status, "reviewable_pending_candidates", 0) or 0) if generation_status else 0
    )
    latest_testable_candidate_id = (
        getattr(generation_status, "latest_testable_candidate_id", "") if generation_status else ""
    )
    testable_pending_candidates = (
        int(getattr(generation_status, "testable_pending_candidates", 0) or 0) if generation_status else 0
    )
    latest_approved_candidate_id = (
        getattr(generation_status, "latest_approved_candidate_id", "") if generation_status else ""
    )
    approved_recipe_path = getattr(generation_status, "latest_approved_recipe_path", "") if generation_status else ""
    approved_matches_source = (
        bool(getattr(generation_status, "approved_matches_source_recipe_path", False)) if generation_status else False
    )
    status = {
        "title": "Ready for setup",
        "summary": "This source is saved, but it still needs a reading plan and a passing source test before the daily run can use it.",
        "badge": "In setup",
        "badge_class": "medium",
        "primary_action": None,
        "secondary_action": {"type": "link", "label": "Edit settings", "href": "#source-settings"},
        "preview_label": _source_test_evidence_label(readiness),
        "source_test_label": _status_label(readiness.readiness_status),
        "automation_label": "Included"
        if execution_enabled
        else "Prepared but off"
        if execution_entry
        else "Not included",
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
                    "title": "Use generated reading plan",
                    "summary": "A generated reading plan is ready to select before running the safe source test.",
                    "badge": "Select plan",
                    "badge_class": "medium",
                    "primary_action": {
                        "type": "link",
                        "label": "Use plan and test",
                        "href": f"/recipe-candidates/{latest_reviewable_candidate_id}?source_id={source.id}",
                    },
                    "blockers": ["No reading plan is selected yet."],
                }
            )
            return status
        if testable_pending_candidates and latest_testable_candidate_id:
            status.update(
                {
                    "title": "Generated reading plan has warnings",
                    "summary": "A generated reading plan has local quality warnings. Select it only if you want the safe source test to be the next gate.",
                    "badge": "Warnings",
                    "badge_class": "medium",
                    "primary_action": {
                        "type": "link",
                        "label": "Use plan and test",
                        "href": f"/recipe-candidates/{latest_testable_candidate_id}?source_id={source.id}",
                    },
                    "blockers": ["No reading plan is selected yet."],
                }
            )
            return status
        if approved_recipe_path and latest_approved_candidate_id and not approved_matches_source:
            status.update(
                {
                    "title": "Use saved reading plan",
                    "summary": "A saved reading plan exists, but this source is not using it yet.",
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
    elif readiness.readiness_status != "ready":
        pagination_issue = _pagination_access_issue(readiness, source=source, session_status=session_status)
        if pagination_issue:
            status.update(
                {
                    "title": pagination_issue["status_title"],
                    "summary": pagination_issue["status_summary"],
                    "badge": pagination_issue["badge"],
                    "badge_class": "medium",
                    "primary_action": pagination_issue.get("action")
                    or {
                        "type": "link",
                        "label": "Review source test",
                        "href": f"/sources/{source.id}/test-run",
                    },
                    "blockers": list(readiness.blockers[:3]),
                }
            )
            return status
        status.update(
            {
                "title": "Test the updated reading plan"
                if bool(getattr(readiness, "checks", {}).get("recipe_changed_after_source_test"))
                else "Ready for a safe source test",
                "summary": "The reading plan changed since the last source test. Run a fresh source test before indexing or daily runs."
                if bool(getattr(readiness, "checks", {}).get("recipe_changed_after_source_test"))
                else "Test this source the way the daily run would use it, without saving job packages or marking postings as seen.",
                "badge": "Needs source test",
                "badge_class": "medium",
                "primary_action": {
                    "type": "link",
                    "label": "Test source safely",
                    "href": f"/sources/{source.id}/test-run?start=1",
                },
                "blockers": list(readiness.blockers[:3]),
            }
        )
    elif not execution_enabled:
        if not index_complete:
            status.update(
                {
                    "title": "Ready to refresh listing index",
                    "summary": "The source test passed, but no listing index is saved yet.",
                    "badge": "Needs index",
                    "badge_class": "medium",
                    "primary_action": {
                        "type": "post",
                        "label": "Refresh listing index",
                        "action": f"/sources/{source.id}/index-listings",
                    },
                }
            )
        else:
            status.update(
                {
                    "title": "Ready to enable",
                    "summary": "The source test passed and listings are indexed. Include it in the daily run when ready.",
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
        if not index_complete:
            status.update(
                {
                    "title": "Included but needs listing index",
                    "summary": "This source has a daily-run entry, but setup is not complete until listings are indexed.",
                    "badge": "Needs index",
                    "badge_class": "medium",
                    "primary_action": {
                        "type": "post",
                        "label": "Refresh listing index",
                        "action": f"/sources/{source.id}/index-listings",
                    },
                }
            )
        else:
            status.update(
                {
                    "title": "Eligible for daily run",
                    "summary": "This source is enabled, its source-test proof is current, and its listing index is ready.",
                    "badge": "Will run",
                    "badge_class": "high",
                    "primary_action": {
                        "type": "post",
                        "label": "Run daily check now",
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
    index_status: dict[str, object] | None = None,
    detail_status: dict[str, object] | None = None,
    session_status=None,
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
    latest_testable_candidate_id = (
        getattr(generation_status, "latest_testable_candidate_id", "") if generation_status else ""
    )
    testable_pending_candidates = (
        int(getattr(generation_status, "testable_pending_candidates", 0) or 0) if generation_status else 0
    )
    execution_enabled = bool(execution_entry and execution_entry.get("enabled", True))
    index_status = index_status or {}
    detail_status = detail_status or {}
    index_complete = bool(index_status.get("complete"))
    detail_complete = bool(detail_status.get("complete"))
    pagination_issue = _pagination_access_issue(readiness, source=source, session_status=session_status)
    steps = [
        {
            "title": "Add source",
            "summary": "The job-board URL is saved.",
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
        learn_badge = "Select"
        learn_state = "active"
        learn_action = {
            "type": "link",
            "label": "Use plan and test",
            "href": f"/recipe-candidates/{latest_reviewable_candidate_id}?source_id={source.id}",
        }
        learn_summary = "A generated reading plan is ready to select before running the safe source test."
    elif testable_pending_candidates and latest_testable_candidate_id:
        learn_badge = "Warnings"
        learn_state = "active"
        learn_action = {
            "type": "link",
            "label": "Inspect warnings",
            "href": f"/recipe-candidates/{latest_testable_candidate_id}?source_id={source.id}",
        }
        learn_summary = "A generated reading plan has local quality warnings."
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
    test_ready = readiness.readiness_status == "ready"
    test_blocked_by_issue = bool(pagination_issue and not test_ready)
    test_issue_action = pagination_issue.get("action") if pagination_issue else None
    default_test_action = (
        {"type": "link", "label": "Test source", "href": f"/sources/{source.id}/test-run?start=1"}
        if source.recipe_path and source.status != "archived" and not test_ready
        else None
    )
    test_action = (
        test_issue_action if source.recipe_path and not test_ready and test_blocked_by_issue else default_test_action
    )
    steps.append(
        {
            "title": "Test safely",
            "summary": pagination_issue["step_summary"]
            if test_blocked_by_issue
            else readiness.readiness_summary
            if source.recipe_path
            else "A source test runs the plan without saving job packages.",
            "badge": "Passed"
            if test_ready
            else str(pagination_issue.get("badge") or "Needs attention")
            if test_blocked_by_issue
            else "Run test"
            if source.recipe_path and source.status != "archived"
            else "Waiting",
            "badge_class": "high" if test_ready else "medium",
            "state": "complete"
            if test_ready
            else "active"
            if test_blocked_by_issue
            else ("active" if source.recipe_path and source.status != "archived" else "blocked"),
            "action": test_action,
        }
    )
    index_available = bool(source.recipe_path and test_ready and source.status != "archived" and not pagination_issue)
    steps.append(
        {
            "title": "Listing index",
            "summary": str(
                index_status.get("summary")
                or "Scan all listing and pagination pages without opening posting details or marking jobs as seen."
            ),
            "badge": "Passed" if index_complete else ("Available" if index_available else "Waiting"),
            "badge_class": "high" if index_complete else "medium",
            "state": "complete" if index_complete else ("active" if index_available else "blocked"),
            "action": (
                {"type": "post", "label": "Refresh listing index", "action": f"/sources/{source.id}/index-listings"}
                if index_available and not index_complete
                else None
            ),
        }
    )
    include_step_verified = bool(execution_enabled and test_ready and not pagination_issue)
    if include_step_verified:
        include_summary = "This source is included in automatic job checks."
        include_badge = "Included"
        include_state = "complete"
    elif execution_enabled:
        include_summary = (
            "This source has a daily-run entry, but it needs a fresh passing source test before it is treated "
            "as implemented."
        )
        include_badge = "Needs retest"
        include_state = "blocked"
    else:
        include_summary = "Keep it off until the source test passes and the listing index has been captured."
        include_badge = "Ready" if test_ready and index_complete else "Off"
        include_state = "active" if test_ready and index_complete else "blocked"
    steps.append(
        {
            "title": "Include in daily run",
            "summary": include_summary,
            "badge": include_badge,
            "badge_class": "high" if include_step_verified or (test_ready and not execution_enabled) else "medium",
            "state": include_state,
            "action": (
                {"type": "post", "label": "Include in daily run", "action": f"/sources/{source.id}/enable-when-ready"}
                if test_ready and index_complete and not execution_enabled
                else None
            ),
        }
    )
    detail_available = bool(index_complete and source.status != "archived" and not pagination_issue)
    steps.append(
        {
            "title": "Initial ingestion",
            "summary": str(
                detail_status.get("summary")
                or "Optional: open details for every indexed job once and add the results to today's daily run when one exists."
            ),
            "badge": "Passed" if detail_complete else ("Available" if detail_available else "Optional"),
            "badge_class": "high" if detail_complete else "medium",
            "state": "complete" if detail_complete else ("active" if detail_available else "blocked"),
            "optional": True,
            "action": (
                {
                    "type": "post",
                    "label": "Ingest all indexed jobs",
                    "action": f"/sources/{source.id}/investigate-all",
                }
                if source.recipe_path and detail_available and not detail_complete
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


def _source_test_evidence_label(readiness) -> str:
    if getattr(readiness, "readiness_status", "") == "ready":
        return "Passed"
    if getattr(readiness, "last_checked_at", ""):
        return "Available"
    return "Waiting"


def _readiness_blockers(readiness) -> list[str]:
    blockers = [str(item).strip() for item in getattr(readiness, "blockers", []) or [] if str(item).strip()]
    if blockers:
        return blockers
    summary = str(getattr(readiness, "readiness_summary", "") or "").strip()
    if summary:
        return [summary]
    return ["Safe source test has not passed for the current reading plan."]


def _pagination_access_issue(readiness, *, source=None, session_status=None) -> dict[str, Any] | None:
    checks = list(getattr(readiness, "dry_run_capability_checks", []) or [])
    readiness_checks = getattr(readiness, "checks", {}) or {}
    blockers = [str(item) for item in getattr(readiness, "blockers", []) or []]
    if isinstance(readiness_checks, dict) and readiness_checks.get("recipe_changed_after_source_test"):
        return None
    if any("reading plan changed since the saved source test" in blocker.lower() for blocker in blockers):
        return None
    if not checks and isinstance(readiness_checks, dict):
        checks = list(readiness_checks.get("source_test_capability_checks") or [])
    failures = [
        check
        for check in checks
        if isinstance(check, dict)
        and str(check.get("capability") or "")
        in {
            "pagination_navigation",
            "listing_total_access",
            "pagination_strategy",
            "ajax_pagination",
            "browser_click_pagination",
            "pagination_duplicate_pages",
            "source_access",
        }
        and str(check.get("status") or "") == "fail"
    ]
    combined = " ".join(
        [str(check.get("detail") or "") for check in failures]
        + blockers
        + [str(warning) for warning in getattr(readiness, "dry_run_warnings", []) or []]
    ).lower()
    if (
        not failures
        and "pagination verification failed" not in combined
        and "source access verification failed" not in combined
        and "connected source session is required" not in combined
    ):
        return None
    if any(token in combined for token in ["logged-in", "login", "sign-in", "session", "auth", "authenticated"]):
        session_label = getattr(session_status, "label", "Not connected") if session_status else "Not connected"
        session_usable = bool(getattr(session_status, "usable", False))
        session_verified = bool(getattr(session_status, "verified_at", ""))
        session_status_value = str(getattr(session_status, "status", "") or "")
        action_label = (
            "Refresh session"
            if session_status_value in {"expired", "missing_state", "unverified"}
            else "Test source safely"
            if session_usable and not session_verified
            else "Refresh session"
            if session_usable
            else "Connect session"
        )
        action_href = (
            f"/sources/{source.id}/test-run?start=1"
            if source is not None and session_usable and not session_verified
            else f"/sources/{source.id}/session"
            if source is not None
            else ""
        )
        return {
            "badge": "Needs session",
            "status_title": "Needs verified source access"
            if session_usable and not session_verified
            else "Needs a connected source session",
            "status_summary": (
                "The app can see some listings, but later pages did not verify without source access. "
                f"Session status: {session_label}. "
                + (
                    "Run the safe source test using the connected session."
                    if session_usable
                    else "Connect a source session, then rerun the safe source test."
                )
            ),
            "step_summary": (
                "Later listing pages appear to require source access. "
                + (
                    "Run the safe source test using the connected session to verify access."
                    if session_usable
                    else "Connect or refresh the source session, then rerun the safe source test."
                )
            ),
            "action": ({"type": "link", "label": action_label, "href": action_href} if source is not None else None),
        }
    if any(token in combined for token in ["client-side", "browser", "click", "rendered"]):
        return {
            "badge": "Needs browser flow",
            "status_title": "Needs pagination flow verification",
            "status_summary": (
                "The app found pagination, but the verified extractor cannot reach later result pages yet. "
                "Update the reading plan to use the required browser flow, then rerun the safe source test."
            ),
            "step_title": "Verify pagination flow",
            "step_summary": (
                "The listing pages need an interactive or rendered pagination strategy. Update the reading plan "
                "and rerun the safe source test before indexing."
            ),
        }
    return {
        "badge": "Needs pagination",
        "status_title": "Needs pagination verification",
        "status_summary": (
            "The app found pagination, but later result pages did not verify. Fix the reading plan or source access, "
            "then rerun the safe source test."
        ),
        "step_title": "Verify pagination access",
        "step_summary": (
            "Later listing pages did not produce unique jobs during verification. Fix pagination access before indexing."
        ),
    }
