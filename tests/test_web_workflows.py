from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from tests.helpers import EURSAP_SOURCE, seed_eursap_source, seed_source_registry

from job_agent.models import Job
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_health_service import SourceHealthService
from job_agent.services.source_listing_index_store import SourceListingIndexStore
from job_agent.services.source_run_field_health_service import (
    SourceRunFieldHealthRecord,
    SourceRunFieldHealthService,
)
from job_agent.services.source_session_service import SourceSessionService
from job_agent.services.source_test_service import SourceTestResult
from job_agent.web.workflows import AppWorkflowHandler


def test_app_workflow_map_connects_core_handlers(project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root)

    workflow_map = handler.map()

    assert set(workflow_map) == {"profile", "source", "recipe", "executor", "applications"}
    assert workflow_map["source"].owner == "SourceWorkflowHandler"
    assert workflow_map["recipe"].owner == "RecipeWorkflowHandler"
    assert workflow_map["profile"].owner == "ProfileWorkflowHandler"
    assert workflow_map["executor"].owner == "ExecutorWorkflowHandler"
    assert workflow_map["applications"].owner == "ApplicationWorkflowHandler"
    assert "recipe" in workflow_map["source"].handoffs
    assert "executor" in workflow_map["source"].handoffs
    assert "source workflow diagnosis" in workflow_map["recipe"].state_inputs
    assert "application records" in workflow_map["applications"].state_inputs
    assert handler.recipe.source is handler.source


def test_source_workflow_overview_uses_source_state(project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root)
    source = handler.source.add_source(name="Example Jobs", url="https://example.com/jobs")

    overview = handler.source.overview_context()

    source_cards = [card for card in overview["source_cards"] if card["source"].id == source.id]
    assert source_cards
    card = source_cards[0]
    assert card["lifecycle"]["state"] == "setup"
    assert card["index"]["complete"] is False
    assert card["detail"]["complete"] is False


def test_source_workflow_overview_does_not_treat_registry_enabled_as_daily_run(project_root: Path) -> None:
    seed_source_registry(project_root, {**EURSAP_SOURCE, "enabled": True})
    handler = AppWorkflowHandler(project_root)

    overview = handler.source.overview_context()

    source_cards = [card for card in overview["source_cards"] if card["source"].id == "eursap-jobs"]
    assert source_cards
    card = source_cards[0]
    assert overview["daily_run_enabled_count"] == 0
    assert card["execution"] is None
    assert card["lifecycle"]["state"] == "setup"
    assert card["status"]["automation_label"] == "Not included"


def test_source_workflow_overview_uses_current_readiness_for_run_eligibility(project_root: Path) -> None:
    seed_eursap_source(project_root)
    handler = AppWorkflowHandler(project_root)
    source = handler.source.require_source("eursap-jobs")
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=True)
    SourceExecutionReadinessService(project_root).save_from_source_test(
        SourceTestResult(
            source_id="eursap-jobs",
            source_name="Eursap Jobs",
            source_type="recipe_html",
            source_enabled=True,
            status="success",
            job_count=1,
        )
    )
    SourceListingIndexStore(project_root).record_index(
        source_id="eursap-jobs",
        source_name="Eursap Jobs",
        jobs=[
            Job(
                title="SAP Basis Consultant",
                source="Eursap Jobs",
                source_id="eursap-jobs",
                url="https://eursap.eu/jobs/sap-basis",
            )
        ],
    )
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    future_timestamp = datetime.now(UTC).timestamp() + 60
    os.utime(recipe_path, (future_timestamp, future_timestamp))

    overview = handler.source.overview_context()

    card = next(card for card in overview["source_cards"] if card["source"].id == "eursap-jobs")
    assert overview["daily_run_enabled_count"] == 1
    assert overview["daily_run_eligible_count"] == 0
    assert overview["daily_run_skipped_count"] == 1
    assert overview["stale_recipe_source_count"] == 1
    assert overview["auto_setup_all"]["stale_refresh_count"] == 1
    assert overview["auto_setup_all"]["learning_count"] == 0
    assert overview["auto_setup_all"]["can_start"] is True
    assert card["lifecycle"]["state"] == "setup"
    assert card["run_eligibility"]["label"] == "Will be skipped"
    assert card["run_eligibility"]["title"] == "Configured but blocked"
    assert card["run_eligibility"]["stale_recipe_source_test"] is True
    assert "Reading plan changed since the saved source test" in card["run_eligibility"]["blockers"][0]
    assert card["auto_setup"]["label"] == "Refresh source test"
    assert card["auto_setup"]["can_start"] is True
    assert card["auto_setup"]["requires_llm"] is False
    assert card["auto_setup"]["stale_recipe_source_test"] is True


def test_source_workflow_overview_blocks_ready_source_when_required_session_is_missing(project_root: Path) -> None:
    seed_eursap_source(project_root)
    handler = AppWorkflowHandler(project_root)
    source = handler.source.require_source("eursap-jobs")
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    recipe_path.write_text(
        "source_name: Eursap Jobs\n"
        "mode: static_html\n"
        "access:\n"
        "  requires_session: true\n"
        "  session_scope: eursap.eu\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a\n"
        "  link_selector: a\n",
        encoding="utf-8",
    )
    session_state = project_root / "sources" / "sessions" / "eursap-jobs.storage-state.json"
    session_state.parent.mkdir(parents=True, exist_ok=True)
    session_state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    session_service = SourceSessionService(project_root)
    session_service.record_storage_state(
        source.id,
        session_scope="eursap.eu",
        storage_state_path="sources/sessions/eursap-jobs.storage-state.json",
    )
    session_service.mark_verified(source.id, session_scope="eursap.eu")
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=True)
    SourceExecutionReadinessService(project_root).save_from_source_test(
        SourceTestResult(
            source_id=source.id,
            source_name=source.name,
            source_type="recipe_html",
            source_enabled=True,
            status="success",
            job_count=1,
            source_access_requires_session=True,
            source_access_session_status="connected",
        )
    )
    SourceListingIndexStore(project_root).record_index(
        source_id=source.id,
        source_name=source.name,
        jobs=[
            Job(
                title="SAP Basis Consultant",
                source=source.name,
                source_id=source.id,
                url="https://eursap.eu/jobs/sap-basis",
            )
        ],
    )
    session_service.clear(source.id)

    overview = handler.source.overview_context()

    card = next(card for card in overview["source_cards"] if card["source"].id == source.id)
    assert overview["daily_run_eligible_count"] == 0
    assert overview["daily_run_skipped_count"] == 1
    assert overview["source_access_attention_count"] == 1
    assert card["access"]["status"] == "needs_login"
    assert card["access"]["action"]["href"] == "/sources/eursap-jobs/session"
    assert card["run_eligibility"]["eligible"] is False
    assert card["run_eligibility"]["label"] == "Will be skipped"
    assert "requires a connected session" in " ".join(card["run_eligibility"]["blockers"])


def test_source_workflow_excludes_setup_complete_disabled_source_from_prepare_all(project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root)
    seed_eursap_source(project_root)
    source = handler.source.require_source("eursap-jobs")
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=False)
    SourceExecutionReadinessService(project_root).save_from_source_test(
        SourceTestResult(
            source_id=source.id,
            source_name=source.name,
            source_type="recipe_html",
            source_enabled=False,
            forced_disabled=True,
            status="success",
            job_count=1,
        )
    )
    SourceListingIndexStore(project_root).record_index(
        source_id=source.id,
        source_name=source.name,
        jobs=[
            Job(
                title="SAP Basis Consultant",
                source=source.name,
                source_id=source.id,
                url="https://eursap.eu/jobs/sap-basis",
            )
        ],
    )

    overview = handler.source.overview_context()
    card = next(card for card in overview["source_cards"] if card["source"].id == source.id)

    assert card["lifecycle"]["state"] == "setup"
    assert card["auto_setup"]["setup_complete"] is True
    assert card["auto_setup"]["label"] == "Setup complete"
    assert card["auto_setup"]["can_start"] is False
    assert overview["auto_setup_all"]["can_start"] is False


def test_source_workflow_reset_learned_state_clears_cross_source_state(project_root: Path) -> None:
    seed_eursap_source(project_root)
    handler = AppWorkflowHandler(project_root)
    source = handler.source.require_source("eursap-jobs")
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=True)
    SourceExecutionReadinessService(project_root).save_from_source_test(
        SourceTestResult(
            source_id=source.id,
            source_name=source.name,
            source_type="recipe_html",
            source_enabled=True,
            status="success",
            job_count=1,
        )
    )
    SourceHealthService(project_root).save_failure(source.id, "sample.html", "local_fixture_html", "old failure")
    SourceListingIndexStore(project_root).record_index(
        source_id=source.id,
        source_name=source.name,
        jobs=[
            Job(
                title="SAP Basis Consultant",
                source=source.name,
                source_id=source.id,
                url="https://eursap.eu/jobs/sap-basis",
            )
        ],
    )
    SourceRunFieldHealthService(project_root).save(
        SourceRunFieldHealthRecord(
            source_id=source.id,
            source_name=source.name,
            status="needs_relearn",
            summary="Descriptions missing.",
            job_count=1,
        )
    )

    updated = handler.source.reset_learned_state(source.id)

    assert updated.kind == "job_board"
    assert updated.status == "needs_review"
    assert updated.recipe_path == ""
    assert updated.enabled is False
    assert "recipe" not in updated.tags
    assert ExecutionSourceService(project_root).find_by_source_id(source.id) is None
    assert SourceExecutionReadinessService(project_root).load(source.id).readiness_status == "untested"
    assert SourceHealthService(project_root).get_health(source.id).health_status == "untested"
    assert SourceListingIndexStore(project_root).summary_for_source(source.id).indexed_count == 0
    assert SourceRunFieldHealthService(project_root).get(source.id).status == "unknown"


def test_source_workflow_overview_avoids_detail_building(monkeypatch, project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root)
    source = handler.source.add_source(name="Example Jobs", url="https://example.com/jobs")

    def fail(*_args, **_kwargs):
        raise AssertionError("The sources overview should not build full source-detail state.")

    monkeypatch.setattr("job_agent.web.source_workflow.SourceWorkflowHandler.build", fail)
    monkeypatch.setattr("job_agent.web.source_workflow.RecipeArtifactService.list_artifacts_for_source", fail)
    monkeypatch.setattr("job_agent.web.source_workflow.RecipeGenerationStatusService.build_for_source", fail)
    monkeypatch.setattr("job_agent.web.source_workflow.SourceSessionService.status_for_source", fail)
    monkeypatch.setattr("job_agent.web.source_workflow.explain_recipe", fail)
    package_scan_count = 0

    def list_saved_packages_once(*_args, **_kwargs):
        nonlocal package_scan_count
        package_scan_count += 1
        return []

    monkeypatch.setattr(
        "job_agent.services.package_index_service.PackageIndexService.list_packages", list_saved_packages_once
    )

    overview = handler.source.overview_context()

    source_cards = [card for card in overview["source_cards"] if card["source"].id == source.id]
    assert source_cards
    assert source_cards[0]["status"]["badge"] == "Needs setup"
    assert package_scan_count >= 1


def test_source_test_pagination_warning_offers_reading_plan_rebuild(project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root).source
    source = SimpleNamespace(id="freelancermap")
    result = SourceTestResult(
        source_id="freelancermap",
        source_name="FreelancerMap",
        status="warning",
        job_count=66,
        warning_count=1,
        warnings=[
            "FreelancerMap: Pagination page pagenr=1 returned only listings already seen on earlier pages. "
            "The source may require a logged-in session or client-side pagination for later result pages."
        ],
        pagination_strategy="url",
        pagination_fetch_count=3,
        pagination_duplicate_page_count=1,
        pagination_duplicate_ratio=0.33,
        pagination_unique_jobs_from_fetched_pages=44,
        interactive_pagination_control_count=7,
        visible_total_job_count=75,
        capability_checks=[
            {
                "capability": "listing_total_access",
                "status": "pass",
                "expected": True,
                "observed": False,
                "detail": "The listing page appears to advertise 75 posting(s); the verified extractor reached 66.",
            },
            {
                "capability": "pagination_duplicate_pages",
                "status": "pass",
                "expected": True,
                "observed": True,
                "detail": "1 fetched pagination page(s) returned only duplicate listings; duplicate ratio 33%.",
            },
        ],
    )

    insight = handler.source_test_insight(source, result=result)

    assert insight["title"] == "Paginated page access needs review"
    assert insight["action"]["label"] == "Rebuild reading plan"
    assert insight["action"]["action"] == "/sources/freelancermap/reading-plan/rebuild-from-test"
    assert insight["generation_clues"]["pagination_warning"]
    assert insight["generation_clues"]["pagination_duplicate_page_count"] == 1
    assert insight["generation_clues"]["interactive_pagination_control_count"] == 7
    assert insight["ai_oversight"]["mode"] == "ai_review_available"
    assert insight["generation_clues"]["ai_oversight"]["bundle_failures"] is False


def test_source_test_incomplete_run_is_not_reported_as_passed(project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root).source
    source = SimpleNamespace(id="freelancermap")
    result = SourceTestResult(
        source_id="freelancermap",
        source_name="FreelancerMap",
        status="failing",
        warning_count=1,
        warnings=["FreelancerMap: Recipe extraction failed: Playwright render failed."],
    )

    insight = handler.source_test_insight(source, result=result)

    assert insight["title"] == "Source test could not complete"
    assert insight["action"]["label"] == "Run source test"
    assert insight["ai_oversight"]["escalation_level"] == 1


def test_source_test_missing_playwright_is_dependency_issue_not_rebuild(project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root).source
    source = SimpleNamespace(id="xing-jobs")
    result = SourceTestResult(
        source_id="xing-jobs",
        source_name="XING Jobs",
        status="warning",
        job_count=19,
        warning_count=1,
        warnings=["XING Jobs: Browser-click pagination requires Playwright: No module named 'playwright'"],
        pagination_strategy="browser_click",
        pagination_fetch_count=0,
        pagination_unique_jobs_from_fetched_pages=0,
        capability_checks=[
            {
                "capability": "pagination_strategy",
                "status": "fail",
                "detail": "Recipe declares browser_click pagination, but no later page was proof-fetched.",
            },
            {
                "capability": "browser_click_pagination",
                "status": "fail",
                "detail": "Browser-click pagination requires a click selector and a proof click.",
            },
        ],
    )

    insight = handler.source_test_insight(source, result=result)

    assert insight["title"] == "Browser support required"
    assert insight["action"]["label"] == "Run source test"
    assert "rebuild-from-test" not in str(insight["action"])
    assert insight["generation_clues"]["ai_oversight"]["escalation_level"] == 2


def test_source_test_passes_when_loop_warning_has_no_duplicate_postings(project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root).source
    source = SimpleNamespace(id="whitehall-sap-contract")
    result = SourceTestResult(
        source_id="whitehall-sap-contract",
        source_name="Whitehall Resources SAP Jobs",
        status="success",
        job_count=40,
        warnings=[
            "Whitehall Resources SAP Jobs: Pagination page https://www.whitehallresources.com/sap-jobs/ "
            "returned only listings already seen on earlier pages."
        ],
        pagination_strategy="url",
        pagination_fetch_count=2,
        pagination_duplicate_page_count=0,
        pagination_duplicate_ratio=0.0,
        pagination_unique_jobs_from_fetched_pages=25,
        capability_checks=[
            {
                "capability": "pagination_strategy",
                "status": "pass",
                "expected": True,
                "observed": True,
                "detail": "Recipe declares url pagination and proof fetched 2 page(s).",
            },
            {
                "capability": "pagination_duplicate_pages",
                "status": "pass",
                "expected": True,
                "observed": False,
                "detail": "0 fetched pagination page(s) contained duplicate listings.",
            },
        ],
    )

    insight = handler.source_test_insight(source, result=result)

    assert insight["title"] == "Source test passed"
    assert insight["action"] == {}
    assert insight["generation_clues"]["pagination_working_with_unique_pages"] is True
    assert insight["generation_clues"]["pagination_duplicate_postings"] is False


def test_source_test_passes_when_total_gap_is_within_verified_threshold(project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root).source
    source = SimpleNamespace(id="freelancermap")
    result = SourceTestResult(
        source_id="freelancermap",
        source_name="FreelancerMap",
        status="success",
        job_count=66,
        pagination_strategy="url",
        pagination_fetch_count=2,
        pagination_duplicate_page_count=0,
        pagination_duplicate_ratio=0.0,
        pagination_unique_jobs_from_fetched_pages=44,
        visible_total_job_count=75,
        capability_checks=[
            {
                "capability": "listing_total_access",
                "status": "pass",
                "expected": True,
                "observed": False,
                "detail": "The listing page appears to advertise 75 posting(s); the verified extractor reached 66.",
            },
            {
                "capability": "pagination_duplicate_pages",
                "status": "pass",
                "expected": True,
                "observed": False,
                "detail": "0 fetched pagination page(s) returned only duplicate listings; duplicate ratio 0%.",
            },
        ],
    )

    insight = handler.source_test_insight(source, result=result)

    assert insight["title"] == "Source test passed"
    assert insight["action"] == {}
