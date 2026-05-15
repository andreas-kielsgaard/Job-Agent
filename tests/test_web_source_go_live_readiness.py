from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_agent.io.yaml_store import read_yaml
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.recipe_preview_service import RecipePreviewResult
from job_agent.services.source_dry_run_service import DryRunJobPreview, SourceDryRunResult
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_health_service import SourceHealthService
from job_agent.services.source_registry_service import SourceRegistryService
from job_agent.web.app import create_app
from job_agent.web.dependencies import reset_root, set_root


@pytest.fixture
def client(project_root: Path, minimal_profile: Path):
    set_root(project_root)
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        reset_root()


def test_source_detail_shows_go_live_readiness_panel(client: TestClient) -> None:
    response = client.get("/sources/eursap-jobs")

    assert response.status_code == 200
    assert "Go-Live Readiness" in response.text
    assert "Go-live readiness is based on the configured execution source dry run" in response.text


def test_web_dry_run_readiness_route_saves_readiness(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    project_root: Path,
) -> None:
    _prepare_good_source(project_root)

    class FakeDryRunService:
        def __init__(self, root):
            pass

        def dry_run(self, source_id, *, force_disabled=False):
            assert source_id == "eursap-jobs"
            assert force_disabled is True
            return _dry_run_result()

    monkeypatch.setattr("job_agent.web.routers.sources.SourceDryRunService", FakeDryRunService)

    response = client.post("/sources/eursap-jobs/dry-run-readiness", follow_redirects=False)

    readiness = SourceExecutionReadinessService(project_root).load("eursap-jobs")
    assert response.status_code == 303
    assert "message=" in response.headers["location"]
    assert readiness.readiness_status == "ready"
    assert readiness.dry_run_job_count == 1


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

    SourceExecutionReadinessService(project_root).save_from_dry_run(_dry_run_result())
    enabled = client.post("/sources/eursap-jobs/enable-when-ready", follow_redirects=False)

    assert enabled.status_code == 303
    assert "message=" in enabled.headers["location"]
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
    return SourceDryRunResult(
        source_id="eursap-jobs",
        source_name="Eursap Jobs",
        source_type="recipe_html",
        source_enabled=False,
        forced_disabled=True,
        status="success",
        job_count=1,
        jobs=[
            DryRunJobPreview(
                title="SAP Basis Consultant",
                url="https://eursap.eu/jobs/sap-basis",
                source="Eursap Jobs",
                source_id="eursap-jobs",
            )
        ],
    )
