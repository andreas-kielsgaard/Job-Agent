from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from job_agent.services.source_test_service import SourceTestResult
from job_agent.web.workflows import AppWorkflowHandler


def test_app_workflow_map_connects_core_handlers(project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root)

    workflow_map = handler.map()

    assert set(workflow_map) == {"profile", "source", "recipe", "executor"}
    assert workflow_map["source"].owner == "SourceWorkflowHandler"
    assert workflow_map["recipe"].owner == "RecipeWorkflowHandler"
    assert workflow_map["profile"].owner == "ProfileWorkflowHandler"
    assert workflow_map["executor"].owner == "ExecutorWorkflowHandler"
    assert "recipe" in workflow_map["source"].handoffs
    assert "executor" in workflow_map["source"].handoffs
    assert "source workflow diagnosis" in workflow_map["recipe"].state_inputs
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


def test_source_workflow_overview_uses_saved_state_only(monkeypatch, project_root: Path) -> None:
    handler = AppWorkflowHandler(project_root)
    source = handler.source.add_source(name="Example Jobs", url="https://example.com/jobs")

    def fail(*_args, **_kwargs):
        raise AssertionError("The sources overview should only read saved source state.")

    monkeypatch.setattr("job_agent.web.source_workflow.SourceWorkflowHandler.build", fail)
    monkeypatch.setattr("job_agent.web.source_workflow.RecipeArtifactService.list_artifacts_for_source", fail)
    monkeypatch.setattr("job_agent.web.source_workflow.RecipeGenerationStatusService.build_for_source", fail)
    monkeypatch.setattr("job_agent.web.source_workflow.RecipeCandidateStore.list_candidates", fail)
    monkeypatch.setattr("job_agent.web.source_workflow.SourceExecutionReadinessService.evaluate", fail)
    monkeypatch.setattr("job_agent.web.source_workflow.SourceSessionService.status_for_source", fail)
    monkeypatch.setattr("job_agent.web.source_workflow.explain_recipe", fail)
    monkeypatch.setattr("job_agent.services.execution_source_service.ExecutionSourceService.list_sources", fail)
    monkeypatch.setattr("job_agent.services.source_registry_service.SourceRegistryService.list_sources", fail)
    package_scan_count = 0

    def list_saved_packages_once(*_args, **_kwargs):
        nonlocal package_scan_count
        package_scan_count += 1
        return []

    monkeypatch.setattr("job_agent.services.package_index_service.PackageIndexService.list_packages", list_saved_packages_once)

    overview = handler.source.overview_context()

    source_cards = [card for card in overview["source_cards"] if card["source"].id == source.id]
    assert source_cards
    assert source_cards[0]["status"]["badge"] == "Needs setup"
    assert package_scan_count == 1


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
