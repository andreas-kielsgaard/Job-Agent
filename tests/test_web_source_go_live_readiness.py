from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.helpers import seed_common_sources

from job_agent.io.yaml_store import read_yaml
from job_agent.models import Job
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.recipe_preview_service import RecipePreviewResult
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_health_service import SourceHealthService
from job_agent.services.source_listing_index_store import SourceListingIndexStore
from job_agent.services.source_registry_service import SourceRegistryService
from job_agent.services.source_test_service import SourceTestJobPreview, SourceTestResult


@pytest.fixture(autouse=True)
def configured_eursap_source(project_root: Path) -> None:
    seed_common_sources(project_root)


def test_source_detail_shows_go_live_readiness_panel(client: TestClient) -> None:
    response = client.get("/sources/eursap-jobs")

    assert response.status_code == 200
    assert "Safe Source Test" in response.text
    assert "This verifies the full source flow without saving job packages" in response.text


def test_web_source_test_route_saves_readiness(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    project_root: Path,
) -> None:
    _prepare_good_source(project_root)

    class FakeSourceTestService:
        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            assert source_id == "eursap-jobs"
            assert force_disabled is True
            return _dry_run_result()

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    response = client.post("/sources/eursap-jobs/test-run")

    readiness = SourceExecutionReadinessService(project_root).load("eursap-jobs")
    index = SourceListingIndexStore(project_root).summary_for_source("eursap-jobs")
    assert response.status_code == 200
    assert response.json()["readiness_status"] == "ready"
    assert response.json()["listing_index"]["job_count"] == 1
    assert readiness.readiness_status == "ready"
    assert readiness.dry_run_job_count == 1
    assert index.indexed_count == 1
    assert index.listings[0].title == "SAP Basis Consultant"
    assert not (project_root / "jobs" / "seen_jobs.json").exists()


def test_web_enable_when_ready_refuses_blocked_then_enables_ready_source(
    client: TestClient,
    project_root: Path,
) -> None:
    _prepare_good_source(project_root)

    blocked = client.post("/sources/eursap-jobs/enable-when-ready", follow_redirects=False)

    assert blocked.status_code == 303
    assert "warning=" in blocked.headers["location"]
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    assert config["sources"][0]["enabled"] is False

    SourceExecutionReadinessService(project_root).save_from_source_test(_dry_run_result())
    not_indexed = client.post("/sources/eursap-jobs/enable-when-ready", follow_redirects=False)

    assert not_indexed.status_code == 303
    assert "Refresh+the+listing+index" in not_indexed.headers["location"]

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
    overview = client.get("/sources")

    assert 'action="/sources/eursap-jobs/enable-when-ready"' in overview.text
    assert 'name="return_to" value="/sources"' in overview.text

    enabled = client.post(
        "/sources/eursap-jobs/enable-when-ready",
        data={"return_to": "/sources"},
        follow_redirects=False,
    )

    assert enabled.status_code == 303
    assert "message=" in enabled.headers["location"]
    assert enabled.headers["location"].startswith("/sources?")
    assert not enabled.headers["location"].startswith("/sources/eursap-jobs")
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    assert config["sources"][0]["enabled"] is True


def _prepare_good_source(project_root: Path) -> None:
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=False)
    SourceHealthService(project_root).save_preview(
        "eursap-jobs",
        RecipePreviewResult(
            recipe_source_name="Eursap Jobs (experimental)",
            recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
            recipe_status="experimental",
            input_type="local artifact",
            input_value="output/recipe-calibration/eursap/page.html",
            base_url="https://eursap.eu/jobs",
            mode_used="local_fixture_html",
            extracted_job_count=1,
            useful_titles=1,
            generic_labels=0,
            unique_urls=1,
            average_description_length=120,
            jobs=[],
            warnings=[],
        ),
    )


def _dry_run_result():
    return SourceTestResult(
        source_id="eursap-jobs",
        source_name="Eursap Jobs",
        source_type="recipe_html",
        source_enabled=False,
        forced_disabled=True,
        status="success",
        job_count=1,
        jobs=[
            SourceTestJobPreview(
                title="SAP Basis Consultant",
                url="https://eursap.eu/jobs/sap-basis",
                source="Eursap Jobs",
                source_id="eursap-jobs",
            )
        ],
    )
