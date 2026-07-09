from __future__ import annotations

from types import SimpleNamespace

from job_agent.web.view_models.source_status import (
    build_source_page_status,
    build_source_run_eligibility,
    build_source_setup_steps,
)


def test_pagination_session_failure_adds_setup_step_and_blocks_indexing() -> None:
    source = SimpleNamespace(
        id="freelancermap-sap",
        kind="job_board",
        status="active",
        url="https://example.com/projects",
        recipe_path="sources/recipes/experimental/freelancermap.yaml",
        health=SimpleNamespace(
            health_status="good",
            health_summary="Reading plan review passed.",
        ),
    )
    readiness = SimpleNamespace(
        readiness_status="blocked",
        readiness_summary="Blocked: Pagination verification failed.",
        blockers=["Pagination verification failed: later pages may require a logged-in session."],
        dry_run_capability_checks=[
            {
                "capability": "pagination_navigation",
                "status": "fail",
                "detail": "Later pages may require a logged-in session or client-side pagination.",
                "expected": True,
                "observed": True,
            }
        ],
        dry_run_warnings=[],
        checks={},
    )

    status = build_source_page_status(
        source,
        {"enabled": False},
        readiness,
    )
    steps = build_source_setup_steps(
        source,
        {"enabled": False},
        readiness,
        None,
    )

    assert status["title"] == "Needs a connected source session"
    assert status["badge"] == "Needs session"
    assert [step["title"] for step in steps] == [
        "Add source",
        "Learn source",
        "Test safely",
        "Listing index",
        "Include in daily run",
        "Initial ingestion",
    ]
    test_step = next(step for step in steps if step["title"] == "Test safely")
    assert test_step["state"] == "active"
    assert test_step["badge"] == "Needs session"
    assert test_step["action"] == {
        "type": "link",
        "label": "Connect session",
        "href": "/sources/freelancermap-sap/session",
    }
    index_step = next(step for step in steps if step["title"] == "Listing index")
    assert index_step["state"] == "blocked"
    assert index_step["action"] is None

    indexed_steps = build_source_setup_steps(
        source,
        {"enabled": False},
        readiness,
        None,
        index_status={"complete": True, "summary": "23 listings indexed."},
    )
    detail_step = next(step for step in indexed_steps if step["title"] == "Initial ingestion")
    assert detail_step["state"] == "blocked"
    assert detail_step["action"] is None


def test_connected_unverified_session_prompts_verification() -> None:
    source = SimpleNamespace(
        id="freelancermap-sap",
        kind="job_board",
        status="active",
        url="https://example.com/projects",
        recipe_path="sources/recipes/experimental/freelancermap.yaml",
        health=SimpleNamespace(health_status="good", health_summary="Reading plan review passed."),
    )
    readiness = SimpleNamespace(
        readiness_status="blocked",
        readiness_summary="Blocked: Source access verification failed.",
        blockers=["Source access verification failed: connected source session is required."],
        dry_run_capability_checks=[
            {
                "capability": "source_access",
                "status": "fail",
                "detail": "Recipe declares that this source requires a connected session.",
            }
        ],
        dry_run_warnings=[],
        checks={},
    )
    session_status = SimpleNamespace(status="connected", label="Connected", usable=True, verified_at="")

    status = build_source_page_status(
        source,
        {"enabled": False},
        readiness,
        session_status=session_status,
    )
    steps = build_source_setup_steps(
        source,
        {"enabled": False},
        readiness,
        None,
        session_status=session_status,
    )

    assert status["title"] == "Needs verified source access"
    assert status["primary_action"] == {
        "type": "link",
        "label": "Test source safely",
        "href": "/sources/freelancermap-sap/test-run?start=1",
    }
    assert "Verify source session" not in [step["title"] for step in steps]
    test_step = next(step for step in steps if step["title"] == "Test safely")
    assert test_step["state"] == "active"
    assert test_step["action"] == {
        "type": "link",
        "label": "Test source safely",
        "href": "/sources/freelancermap-sap/test-run?start=1",
    }


def test_sign_in_gate_failure_prompts_session_not_recipe_update() -> None:
    source = SimpleNamespace(
        id="freelancermap-sap",
        kind="job_board",
        status="active",
        url="https://example.com/projects",
        recipe_path="sources/recipes/experimental/freelancermap.yaml",
        health=SimpleNamespace(health_status="good", health_summary="Reading plan review passed."),
    )
    readiness = SimpleNamespace(
        readiness_status="blocked",
        readiness_summary="Blocked: Source access verification failed.",
        blockers=["Source access verification failed: the page still showed a sign-in gate."],
        dry_run_capability_checks=[
            {
                "capability": "source_access",
                "status": "fail",
                "detail": "The page still showed a sign-in gate.",
            }
        ],
        dry_run_warnings=[],
        checks={},
    )

    status = build_source_page_status(
        source,
        {"enabled": False},
        readiness,
    )

    assert status["title"] == "Needs a connected source session"
    assert status["primary_action"]["href"] == "/sources/freelancermap-sap/session"


def test_indexing_waits_for_safe_source_test_even_without_pagination_issue() -> None:
    source = SimpleNamespace(
        id="sample-source",
        kind="job_board",
        status="active",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/sample.yaml",
        health=SimpleNamespace(health_status="good", health_summary="Reading plan review passed."),
    )
    readiness = SimpleNamespace(
        readiness_status="untested",
        readiness_summary="No source test readiness has been saved yet.",
        blockers=["No saved source test readiness result."],
        dry_run_capability_checks=[],
        dry_run_warnings=[],
        checks={},
    )

    steps = build_source_setup_steps(
        source,
        {"enabled": False},
        readiness,
        None,
    )

    index_step = next(step for step in steps if step["title"] == "Listing index")
    assert index_step["state"] == "blocked"
    assert index_step["action"] is None


def test_enabled_source_with_stale_readiness_is_not_setup_complete() -> None:
    source = SimpleNamespace(
        id="sample-source",
        kind="job_board",
        status="active",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/sample.yaml",
        health=SimpleNamespace(health_status="good", health_summary="Reading plan review passed."),
    )
    readiness = SimpleNamespace(
        readiness_status="blocked",
        readiness_summary="Blocked: reading plan changed.",
        blockers=["Reading plan changed since the saved source test; rerun the safe source test."],
        dry_run_capability_checks=[],
        dry_run_warnings=[],
        checks={},
    )

    steps = build_source_setup_steps(
        source,
        {"enabled": True},
        readiness,
        None,
    )

    include_step = next(step for step in steps if step["title"] == "Include in daily run")
    assert include_step["state"] == "blocked"
    assert include_step["badge"] == "Needs retest"
    assert include_step["action"] is None


def test_enabled_source_with_stale_readiness_is_configured_but_not_eligible() -> None:
    source = SimpleNamespace(
        id="sample-source",
        kind="job_board",
        status="active",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/sample.yaml",
    )
    readiness = SimpleNamespace(
        readiness_status="blocked",
        readiness_summary="Blocked: reading plan changed.",
        blockers=["Reading plan changed since the saved source test; rerun the safe source test."],
    )

    eligibility = build_source_run_eligibility(
        source,
        {"enabled": True},
        readiness,
        index_status={"complete": True},
    )

    assert eligibility["configured"] is True
    assert eligibility["enabled"] is True
    assert eligibility["eligible"] is False
    assert eligibility["label"] == "Will be skipped"
    assert eligibility["title"] == "Configured but blocked"
    assert "Reading plan changed since the saved source test" in eligibility["blockers"][0]


def test_enabled_source_with_current_readiness_and_index_is_eligible() -> None:
    source = SimpleNamespace(
        id="sample-source",
        kind="job_board",
        status="active",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/sample.yaml",
    )
    readiness = SimpleNamespace(
        readiness_status="ready",
        readiness_summary="Ready.",
        blockers=[],
    )

    eligibility = build_source_run_eligibility(
        source,
        {"enabled": True},
        readiness,
        index_status={"complete": True},
    )

    assert eligibility["eligible"] is True
    assert eligibility["label"] == "Will run"
    assert eligibility["title"] == "Eligible now"


def test_enabled_source_with_current_readiness_index_and_blocked_access_is_not_eligible() -> None:
    source = SimpleNamespace(
        id="sample-source",
        kind="job_board",
        status="active",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/sample.yaml",
    )
    readiness = SimpleNamespace(
        readiness_status="ready",
        readiness_summary="Ready.",
        blockers=[],
    )

    eligibility = build_source_run_eligibility(
        source,
        {"enabled": True},
        readiness,
        index_status={"complete": True},
        source_access={
            "show": True,
            "can_execute": False,
            "blockers": ["example.com requires a connected session."],
        },
    )

    assert eligibility["eligible"] is False
    assert eligibility["access_ready"] is False
    assert eligibility["label"] == "Will be skipped"
    assert "requires a connected session" in eligibility["blockers"][0]


def test_stale_pagination_failure_prompts_retest_not_regeneration() -> None:
    source = SimpleNamespace(
        id="sample-source",
        kind="job_board",
        status="active",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/sample.yaml",
        health=SimpleNamespace(health_status="good", health_summary="Reading plan review passed."),
    )
    readiness = SimpleNamespace(
        readiness_status="blocked",
        readiness_summary="Blocked: Reading plan changed since the saved source test; rerun the safe source test.",
        blockers=["Reading plan changed since the saved source test; rerun the safe source test."],
        dry_run_capability_checks=[
            {
                "capability": "pagination_strategy",
                "status": "fail",
                "detail": "URL pagination returned only duplicate listings.",
            }
        ],
        dry_run_warnings=[],
        checks={"recipe_changed_after_source_test": True},
    )

    status = build_source_page_status(
        source,
        {"enabled": False},
        readiness,
    )
    steps = build_source_setup_steps(
        source,
        {"enabled": False},
        readiness,
        None,
    )

    assert status["title"] == "Test the updated reading plan"
    assert status["primary_action"]["href"] == "/sources/sample-source/test-run?start=1"
    assert "Verify pagination access" not in [step["title"] for step in steps]
    test_step = next(step for step in steps if step["title"] == "Test safely")
    assert test_step["action"]["href"] == "/sources/sample-source/test-run?start=1"
