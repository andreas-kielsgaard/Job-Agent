from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from job_agent.io.yaml_store import read_yaml
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.recipe_suggestion_service import (
    RecipeRefinementAttempt,
    RecipeRefinementResult,
    RecipeSuggestionResult,
)
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_listing_index_store import SourceListingIndexStore
from job_agent.services.source_registry_service import SourceRegistryService
from job_agent.services.source_test_service import SourceTestJobPreview, SourceTestResult
from job_agent.web.source_auto_setup import SourceAutoSetupWorkflowHandler

VALID_RECIPE_YAML = """source_name: Example Jobs
start_url: https://example.com/jobs
mode: static_html
listing:
  card_selector: a.job-card
  title_selector: a.job-card
  link_selector: a.job-card
limits:
  max_cards: 10
"""


def test_auto_setup_learns_adopts_and_safe_tests_without_ingestion(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="Example Jobs",
        url="https://example.com/jobs",
    )
    artifact = _write_artifact(project_root)

    def fake_capture(*args, **kwargs):
        return SimpleNamespace(
            artifact_dir=artifact,
            warnings=[],
            candidate_count=1,
            recipe_extracted_count=1,
            detail_sample_url="",
        )

    monkeypatch.setattr("job_agent.services.recipe_generation_run_service.capture_recipe_calibration", fake_capture)
    monkeypatch.setattr(
        "job_agent.services.recipe_generation_run_service.suggest_recipe_with_refinement",
        lambda artifact_path, **kwargs: _refinement(artifact_path),
    )

    class FakeSourceTestService:
        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            assert source_id == source.id
            assert force_disabled is True
            return SourceTestResult(
                source_id=source.id,
                source_name="Example Jobs",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="success",
                job_count=1,
                jobs=[
                    SourceTestJobPreview(
                        title="SAP Basis Consultant",
                        url="https://example.com/jobs/sap-basis",
                        source="Example Jobs",
                        source_id=source.id,
                    )
                ],
            )

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    workflow = SourceAutoSetupWorkflowHandler(project_root)
    run = workflow.prepare(source.id)
    result = workflow.run(run["run_id"])

    updated = SourceRegistryService(project_root).get_source(source.id)
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    readiness = SourceExecutionReadinessService(project_root).load(source.id)
    index = SourceListingIndexStore(project_root).summary_for_source(source.id)
    assert result["status"] == "completed"
    assert result["recipe_attempts"] == 1
    assert updated.recipe_path == "sources/recipes/experimental/example-jobs.yaml"
    assert config["sources"][0]["enabled"] is False
    assert readiness.readiness_status == "ready"
    assert index.indexed_count == 1
    assert not (project_root / "jobs" / "seen_jobs.json").exists()


def test_auto_setup_regenerates_when_source_test_proposes_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "example-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(VALID_RECIPE_YAML, encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="Example Jobs",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/example-jobs.yaml",
    )
    calls: list[dict[str, object]] = []

    def fake_generate(self, run_id, source_entry, *, source_test_insight, progress_callback=None):
        calls.append(source_test_insight)
        run = self.load(run_id)
        return self._update_run(
            run_id,
            recipe_attempts=int(run.get("recipe_attempts") or 0) + 1,
            last_recipe_path=source_entry.recipe_path,
            last_recipe_run_id=f"fake-run-{len(calls)}",
            last_candidate_id=f"fake-candidate-{len(calls)}",
            stage="Reading plan selected",
            message="Fake regenerated recipe.",
            progress_percent=62,
        )

    monkeypatch.setattr(SourceAutoSetupWorkflowHandler, "_generate_and_adopt_recipe", fake_generate)

    class FakeSourceTestService:
        calls = 0

        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            self.__class__.calls += 1
            if self.__class__.calls == 1:
                return SourceTestResult(
                    source_id=source_id,
                    source_name="Example Jobs",
                    source_type="recipe_html",
                    source_enabled=False,
                    forced_disabled=True,
                    status="warning",
                    job_count=3,
                    pagination_strategy="url",
                    pagination_fetch_count=1,
                    pagination_duplicate_ratio=1.0,
                    pagination_unique_jobs_from_fetched_pages=0,
                    capability_checks=[
                        {
                            "capability": "pagination_strategy",
                            "status": "fail",
                            "detail": "Recipe declares url pagination, but proof-fetched pages returned only duplicate listings.",
                        }
                    ],
                )
            return SourceTestResult(
                source_id=source_id,
                source_name="Example Jobs",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="success",
                job_count=1,
                jobs=[
                    SourceTestJobPreview(
                        title="SAP Basis Consultant",
                        url="https://example.com/jobs/sap-basis",
                        source="Example Jobs",
                        source_id=source_id,
                    )
                ],
            )

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    workflow = SourceAutoSetupWorkflowHandler(project_root)
    run = workflow.prepare(source.id)
    result = workflow.run(run["run_id"])

    assert result["status"] == "completed"
    assert result["source_test_attempts"] == 2
    assert len(calls) == 1
    assert calls[0]["insight_title"] == "Paginated page access failed"
    assert ExecutionSourceService(project_root).find_by_source_id(source.id)["enabled"] is False


def test_auto_setup_blocks_missing_playwright_without_regeneration(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "example-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(VALID_RECIPE_YAML, encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="Example Jobs",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/example-jobs.yaml",
    )

    def fail_generate(*args, **kwargs):
        raise AssertionError("Missing Playwright should not trigger automatic recipe regeneration.")

    monkeypatch.setattr(SourceAutoSetupWorkflowHandler, "_generate_and_adopt_recipe", fail_generate)

    class FakeSourceTestService:
        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            return SourceTestResult(
                source_id=source_id,
                source_name="Example Jobs",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="warning",
                warning_count=1,
                warnings=["Example Jobs: Browser-click pagination requires Playwright: No module named 'playwright'"],
                job_count=3,
                pagination_strategy="browser_click",
                pagination_fetch_count=0,
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

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    workflow = SourceAutoSetupWorkflowHandler(project_root)
    run = workflow.prepare(source.id)
    result = workflow.run(run["run_id"])

    assert result["status"] == "blocked"
    assert result["source_test_attempts"] == 1
    assert result["recipe_attempts"] == 0
    assert "Install the optional Playwright dependencies" in result["message"]


def test_auto_setup_refreshes_existing_recipe_without_llm_key(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
) -> None:
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "example-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(VALID_RECIPE_YAML, encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="Example Jobs",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/example-jobs.yaml",
    )

    def fail_generate(*args, **kwargs):
        raise AssertionError("Existing reading plan refresh should not learn a new recipe first.")

    monkeypatch.setattr(SourceAutoSetupWorkflowHandler, "_generate_and_adopt_recipe", fail_generate)

    class FakeSourceTestService:
        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            assert source_id == source.id
            assert force_disabled is True
            return SourceTestResult(
                source_id=source.id,
                source_name="Example Jobs",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="success",
                job_count=1,
                jobs=[
                    SourceTestJobPreview(
                        title="SAP Basis Consultant",
                        url="https://example.com/jobs/sap-basis",
                        source="Example Jobs",
                        source_id=source.id,
                    )
                ],
            )

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    workflow = SourceAutoSetupWorkflowHandler(project_root)
    run = workflow.prepare(source.id)
    result = workflow.run(run["run_id"])

    assert result["status"] == "completed"
    assert result["recipe_attempts"] == 0
    assert result["source_test_attempts"] == 1
    assert SourceExecutionReadinessService(project_root).load(source.id).readiness_status == "ready"
    assert SourceListingIndexStore(project_root).summary_for_source(source.id).indexed_count == 1


def test_auto_setup_refresh_preserves_enabled_execution_source(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
) -> None:
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "example-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(VALID_RECIPE_YAML, encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="Example Jobs",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/example-jobs.yaml",
    )
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=True)

    class FakeSourceTestService:
        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            return SourceTestResult(
                source_id=source_id,
                source_name="Example Jobs",
                source_type="recipe_html",
                source_enabled=True,
                forced_disabled=True,
                status="success",
                job_count=1,
                jobs=[
                    SourceTestJobPreview(
                        title="SAP Basis Consultant",
                        url="https://example.com/jobs/sap-basis",
                        source="Example Jobs",
                        source_id=source_id,
                    )
                ],
            )

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    workflow = SourceAutoSetupWorkflowHandler(project_root)
    run = workflow.prepare(source.id)
    result = workflow.run(run["run_id"])

    assert result["status"] == "completed"
    assert "remains included in daily runs" in result["message"]
    assert ExecutionSourceService(project_root).find_by_source_id(source.id)["enabled"] is True


def test_auto_setup_retries_transient_source_test_warnings(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
) -> None:
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "sap-contractors.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(VALID_RECIPE_YAML, encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="SAP Contractors",
        url="https://www.sapcontractors.com/search-jobs/?fwp_keyword=",
        recipe_path="sources/recipes/experimental/sap-contractors.yaml",
    )

    class FakeSourceTestService:
        calls = 0

        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            self.__class__.calls += 1
            jobs = [
                SourceTestJobPreview(
                    title="SAP Program Manager",
                    url="https://www.sapcontractors.com/job-role/sap-program-manager-2/",
                    source="SAP Contractors",
                    source_id=source_id,
                )
            ]
            if self.__class__.calls == 1:
                return SourceTestResult(
                    source_id=source_id,
                    source_name="SAP Contractors",
                    source_type="recipe_html",
                    source_enabled=False,
                    forced_disabled=True,
                    status="warning",
                    job_count=10,
                    warnings=[
                        "SAP Contractors: Detail fetch failed for https://www.sapcontractors.com/job-role/sap-program-manager-2/: "
                        "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
                    ],
                    jobs=jobs,
                )
            return SourceTestResult(
                source_id=source_id,
                source_name="SAP Contractors",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="success",
                job_count=10,
                jobs=jobs,
            )

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    workflow = SourceAutoSetupWorkflowHandler(project_root)
    run = workflow.prepare(source.id)
    result = workflow.run(run["run_id"])

    assert result["status"] == "completed"
    assert result["source_test_attempts"] == 2
    assert result["recipe_attempts"] == 0
    assert FakeSourceTestService.calls == 2
    assert SourceExecutionReadinessService(project_root).load(source.id).readiness_status == "ready"


def test_auto_setup_reports_rendered_mode_dependency_blocker(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="Energize Recruitment",
        url="https://energize.example/jobs",
    )

    def fail_capture(*args, **kwargs):
        raise RuntimeError(
            "Rendered mode requested but Playwright is unavailable. "
            "Install requirements-playwright.txt and Chromium to use rendered_html recipes."
        )

    monkeypatch.setattr(
        "job_agent.web.source_auto_setup.RecipeGenerationRunService.start_from_source_capture",
        fail_capture,
    )
    monkeypatch.setattr("job_agent.web.source_auto_setup._rendered_browser_available", lambda: False)

    workflow = SourceAutoSetupWorkflowHandler(project_root)
    run = workflow.prepare(source.id)
    result = workflow.run(run["run_id"])

    assert result["status"] == "blocked"
    assert result["stage"] == "Browser support required"
    assert "Playwright/Chromium is not available" in result["message"]
    monitor = workflow.monitor_context(source_id=source.id)
    assert monitor["auto_setup_runs"][0]["applied_label"] == "Browser support required"
    assert "Playwright/Chromium" in monitor["auto_setup_runs"][0]["applied_summary"]


def test_auto_setup_monitor_marks_old_dependency_blocker_retry_ready(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="Energize Recruitment",
        url="https://energize.example/jobs",
    )
    monkeypatch.setattr("job_agent.web.source_auto_setup._rendered_browser_available", lambda: True)
    workflow = SourceAutoSetupWorkflowHandler(project_root)
    run = workflow.prepare(source.id)
    workflow._finish(
        run["run_id"],
        status="blocked",
        stage="Browser support required",
        message="Rendered mode requested but Playwright is unavailable.",
        error_message="Install requirements-playwright.txt and Chromium to use rendered_html recipes.",
        progress_percent=100,
    )

    monitor = workflow.monitor_context(source_id=source.id)

    assert monitor["auto_setup_runs"][0]["stage"] == "Retry ready"
    assert monitor["auto_setup_runs"][0]["applied_label"] == "Retry ready"
    assert "Continue automatic setup" in monitor["auto_setup_runs"][0]["applied_summary"]


def test_auto_setup_monitor_shows_one_latest_lane_per_source(project_root: Path) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="Example Jobs",
        url="https://example.com/jobs",
    )
    other = SourceRegistryService(project_root).add_source(
        name="Other Jobs",
        url="https://other.example/jobs",
    )
    workflow = SourceAutoSetupWorkflowHandler(project_root)

    older = workflow.prepare(source.id)
    workflow._finish(
        older["run_id"],
        status="failed",
        stage="Old failure",
        message="Older failed attempt.",
        progress_percent=100,
    )
    newer = workflow.prepare(source.id)
    workflow._finish(
        newer["run_id"],
        status="blocked",
        stage="New blocker",
        message="Latest saved attempt.",
        progress_percent=100,
    )
    other_run = workflow.prepare(other.id)

    monitor = workflow.monitor_context()
    source_items = [item for item in monitor["auto_setup_runs"] if item["source_id"] == source.id]

    assert len(source_items) == 1
    assert source_items[0]["run_id"] == newer["run_id"]
    assert {item["source_id"] for item in monitor["auto_setup_runs"]} == {source.id, other.id}
    assert monitor["auto_setup_summary"]["total"] == 2
    assert monitor["auto_setup_summary"]["running"] == 1
    assert other_run["status"] == "pending"


def test_auto_setup_does_not_retest_unchanged_recipe_after_failed_source_test(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "example-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(VALID_RECIPE_YAML, encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="Example Jobs",
        url="https://example.com/jobs",
        recipe_path="sources/recipes/experimental/example-jobs.yaml",
    )

    def fake_generate(self, run_id, source_entry, *, source_test_insight, progress_callback=None):
        run = self.load(run_id)
        return self._update_run(
            run_id,
            recipe_attempts=int(run.get("recipe_attempts") or 0) + 1,
            last_recipe_path=source_entry.recipe_path,
            stage="Reading plan selected",
            message="Fake regenerated recipe without new evidence.",
            progress_percent=62,
        )

    monkeypatch.setattr(SourceAutoSetupWorkflowHandler, "_generate_and_adopt_recipe", fake_generate)

    class FakeSourceTestService:
        calls = 0

        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            self.__class__.calls += 1
            return SourceTestResult(
                source_id=source_id,
                source_name="Example Jobs",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="warning",
                job_count=3,
                pagination_strategy="url",
                pagination_fetch_count=1,
                pagination_duplicate_ratio=1.0,
                pagination_unique_jobs_from_fetched_pages=0,
                capability_checks=[
                    {
                        "capability": "pagination_strategy",
                        "status": "fail",
                        "detail": "Recipe declares url pagination, but proof-fetched pages returned only duplicates.",
                    }
                ],
            )

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    workflow = SourceAutoSetupWorkflowHandler(project_root)
    run = workflow.prepare(source.id)
    result = workflow.run(run["run_id"])

    assert result["status"] == "blocked"
    assert result["source_test_attempts"] == 1
    assert "did not produce a new reading plan" in result["message"]
    assert FakeSourceTestService.calls == 1


def test_auto_setup_blocks_homepage_until_listing_url_is_saved(project_root: Path) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="Example",
        url="https://example.com",
    )

    with pytest.raises(ValueError, match="site homepage"):
        SourceAutoSetupWorkflowHandler(project_root).prepare(source.id)


def test_auto_setup_blocks_disqualified_domains(project_root: Path) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(
        name="LinkedIn Recruiter Posts",
        url="https://www.linkedin.com/jobs/search",
    )

    with pytest.raises(ValueError, match="disqualified"):
        SourceAutoSetupWorkflowHandler(project_root).prepare(source.id)


def test_auto_setup_ui_is_api_key_gated_and_offers_continue(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    project_root: Path,
) -> None:
    source = SourceRegistryService(project_root).add_source(
        name="Example Jobs",
        url="https://example.com/jobs",
    )

    no_key_detail = client.get(f"/sources/{source.id}")
    assert no_key_detail.status_code == 200
    assert "Automatic Setup" in no_key_detail.text
    assert "Add API key" in no_key_detail.text
    button = re.search(r"<button[^>]*>Automatically set up</button>", no_key_detail.text)
    assert button and "disabled" in button.group(0)

    no_key_overview = client.get("/sources")
    assert no_key_overview.status_code == 200
    assert "Automatically set up" in no_key_overview.text
    assert "Add an Anthropic API key in Setup before learning a reading plan." in no_key_overview.text

    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    new_source = client.get("/sources/new")
    checkbox = re.search(r'<input type="checkbox" name="auto_setup"[^>]*>', new_source.text)
    assert checkbox and "disabled" not in checkbox.group(0)

    configured_overview = client.get("/sources")
    overview_button = re.search(r'<form method="post" action="/sources/example-jobs/auto-setup/start"[\s\S]*?<button[^>]*>Automatically set up</button>', configured_overview.text)
    assert overview_button and "disabled" not in overview_button.group(0)

    run = SourceAutoSetupWorkflowHandler(project_root).prepare(source.id)
    continue_detail = client.get(f"/sources/{source.id}")
    assert "Continue automatic setup" in continue_detail.text
    assert f'name="run_id" value="{run["run_id"]}"' in continue_detail.text

    class FakeTask:
        source_name = "Example Jobs"

    monkeypatch.setattr(
        "job_agent.web.routers.sources.runtime.launch_source_auto_setup",
        lambda source_id, run_id="", llm_model="": FakeTask(),
    )
    response = client.post(f"/sources/{source.id}/auto-setup/start", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/sources/auto-setup?")
    assert f"source_id={source.id}" in response.headers["location"]

    monitor = client.get("/sources/auto-setup")
    assert monitor.status_code == 200
    assert "Automatic Source Preparation" in monitor.text
    assert "Source Results" in monitor.text


def test_prepare_all_redirects_before_queueing_and_api_starts_work(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    calls: list[str] = []

    class FakeTask:
        source_name = "Example Jobs"

    def fake_start_all(*, llm_model=""):
        calls.append(llm_model)
        return [FakeTask()]

    monkeypatch.setattr("job_agent.web.routers.sources.runtime.launch_all_source_auto_setups", fake_start_all)

    response = client.post(
        "/sources/auto-setup/start-all",
        data={"llm_model": "claude-test-model"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/sources/auto-setup?")
    assert "queue=all" in response.headers["location"]
    assert calls == []

    api_response = client.post("/api/sources/auto-setup/start-all", data={"llm_model": "claude-test-model"})

    assert api_response.status_code == 200
    assert api_response.json()["queued_count"] == 1
    assert calls == ["claude-test-model"]


def _write_artifact(project_root: Path) -> Path:
    artifact = project_root / "output" / "recipe-calibration" / "example-artifact"
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "summary.md").write_text("# Example Jobs\n", encoding="utf-8")
    (artifact / "page.html").write_text(
        '<a class="job-card" href="/jobs/sap-basis">SAP Basis Consultant</a>',
        encoding="utf-8",
    )
    (artifact / "selector-report.json").write_text(
        json.dumps(
            {
                "url": "https://example.com/jobs",
                "capture_mode": "static_html",
                "candidates": [{"selector": "a.job-card"}],
            }
        ),
        encoding="utf-8",
    )
    return artifact


def _suggestion(artifact: Path) -> RecipeSuggestionResult:
    return RecipeSuggestionResult(
        source_name="Example Jobs",
        start_url="https://example.com/jobs",
        artifact_dir=artifact,
        suggested_recipe_yaml=VALID_RECIPE_YAML,
        confidence="high",
        selected_strategy="selector_based",
        evidence_summary="candidate selectors: a.job-card",
        referenced_artifact_files=["summary.md", "selector-report.json", "page.html"],
        schema_valid=True,
    )


def _refinement(artifact: Path) -> RecipeRefinementResult:
    return RecipeRefinementResult(
        final_result=_suggestion(artifact),
        attempts=[
            RecipeRefinementAttempt(
                attempt_number=1,
                suggested_recipe_yaml=VALID_RECIPE_YAML,
                schema_valid=True,
                validation_errors=[],
                quality_status="good",
                quality_warnings=[],
                extracted_job_count=1,
                useful_titles=1,
                generic_labels=0,
                unique_urls=1,
                average_description_length=100,
            )
        ],
        accepted=True,
    )
