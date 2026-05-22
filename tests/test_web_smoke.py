from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_agent.run_store import RunEvent, RunOptions, RunStore


def test_app_creation_health_and_basic_routes_use_temp_root(client: TestClient, project_root: Path) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    for path in [
        "/",
        "/runs",
        "/jobs",
        "/stats",
        "/setup",
        "/postings/new",
        "/compatibility",
        "/recipe-preview",
        "/sources",
    ]:
        assert client.get(path).status_code == 200

    assert (project_root / "output" / "runs" / "runs.json").exists()
    dashboard = client.get("/").text
    material_checkbox = re.search(r'<input[^>]+name="generate_materials_option"[^>]*>', dashboard)
    assert material_checkbox
    assert "checked" not in material_checkbox.group(0)


def test_missing_resources_and_invalid_bulk_actions_return_errors(client: TestClient) -> None:
    assert client.get("/runs/nonexistent").status_code == 404
    assert client.get("/jobs/nonexistent").status_code == 404
    assert client.post("/api/runs/bulk", data={"run_ids": ["x"], "action": "explode"}).status_code == 400
    assert client.post("/api/jobs/bulk-status", data={"job_ids": ["x"], "status": "maybe"}).status_code == 400


def test_setup_routes_write_to_temp_root_and_validate_inputs(client: TestClient, project_root: Path) -> None:
    contact_response = client.post(
        "/setup/contact",
        data={"name": "Temp User", "email": "temp@example.com", "city": "Aarhus"},
        follow_redirects=False,
    )
    assert contact_response.status_code == 303
    assert "Temp User" in (project_root / "profile" / "contact.yaml").read_text(encoding="utf-8")

    invalid_source = client.post(
        "/setup/source-add",
        data={"name": "Bad", "source_type": "generic_html", "url_or_path": ""},
        follow_redirects=False,
    )
    assert invalid_source.status_code == 400

    local_source = client.post(
        "/setup/source-add",
        data={"name": "Local", "source_type": "local_yaml", "url_or_path": "jobs/raw/manual.yaml"},
        follow_redirects=False,
    )
    assert local_source.status_code == 303
    source_yaml = (project_root / "sources" / "recruiting-sites.yaml").read_text(encoding="utf-8")
    assert "path: jobs/raw/manual.yaml" in source_yaml
    assert "url:" not in source_yaml


def test_unsupported_cv_upload_suffix_returns_400(client: TestClient) -> None:
    response = client.post(
        "/setup/cv-reference",
        files={"cv_file": ("cv.exe", b"not a cv", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_run_detail_renders_source_progress(client: TestClient, project_root: Path) -> None:
    store = RunStore(project_root)
    run = store.create_run(RunOptions())
    store.update(run.run_id, status="running")
    store.append_event(
        RunEvent(
            run_id=run.run_id,
            event_type="source_started",
            message="Checking source 1/1: Local",
            phase="source_ingestion",
            current_source="Local",
            counts={"source_index": 1, "source_count": 1, "jobs_found": 0, "warnings_count": 0},
        )
    )
    store.append_event(
        RunEvent(
            run_id=run.run_id,
            event_type="match_highlight",
            message="Highlighted match: SAP ABAP Consultant - 90% - strong match category",
            phase="scoring",
            current_source="Local",
            current_job="SAP ABAP Consultant",
            counts={"score": 90, "source_index": 1, "source_count": 1, "highlight_count": 1},
        )
    )

    response = client.get(f"/runs/{run.run_id}")

    assert response.status_code == 200
    assert "Source Progress" in response.text
    assert "Interesting Finds" in response.text
    assert "Local" in response.text
    assert "Checking source 1/1: Local" in response.text
    assert "Highlighted match" in response.text


def test_batch_generate_route_redirects_with_counts(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    class FakeResult:
        succeeded = 2
        failed = 1

    class FakeMaterialService:
        def generate_many(self, job_ids, use_llm):
            assert job_ids == ["stable-1", "stable-2", "missing"]
            assert use_llm is True
            return FakeResult()

    monkeypatch.setattr("job_agent.web.routers.jobs.material_service", lambda: FakeMaterialService())

    response = client.post(
        "/api/jobs/batch-generate",
        data={
            "job_ids": ["stable-1", "stable-2", "missing"],
            "use_llm": "on",
            "return_to": "/runs/run-1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/runs/run-1?generated=2&failed=1"


def test_manual_posting_route_creates_package_and_redirects(client: TestClient, project_root: Path) -> None:
    response = client.post(
        "/postings/new",
        data={
            "title": "SAP ABAP Consultant",
            "source": "Recruiter Mail",
            "company": "Client",
            "url": "https://example.com/posting",
            "description": "ABAP RAP CDS OData Gateway role",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/jobs/")
    assert list((project_root / "output").glob("*/*/index.json"))
    manual_yaml = project_root / "jobs" / "manual" / "manual_jobs.yaml"
    assert manual_yaml.exists()


def test_compatibility_route_renders_report(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    report = {
        "url": "https://example.com/jobs",
    }
    monkeypatch.setattr(
        "job_agent.web.routers.compatibility.check_job_board_compatibility",
        lambda url, render: type(
            "Report",
            (),
            {
                "url": url,
                "normal_html": type(
                    "Quality",
                    (),
                    {
                        "label": "Normal HTML",
                        "candidate_count": 0,
                        "useful_title_count": 0,
                        "generic_title_count": 0,
                        "unique_url_count": 0,
                        "average_description_length": 0,
                        "status_code": 200,
                        "visible_text_chars": 0,
                        "warnings": [],
                        "candidates": [],
                    },
                )(),
                "rendered_page": None,
                "recommendation": "manual intake recommended",
                "recommendation_reason": "No candidates.",
                "boundaries": ["Fetched only the provided URL with a polite timeout."],
                "as_dict": lambda self: report,
            },
        )(),
    )

    response = client.post("/compatibility", data={"url": "https://example.com/jobs"}, follow_redirects=False)

    assert response.status_code == 200
    assert "manual intake recommended" in response.text
    assert "Normal HTML" in response.text


def test_recipe_preview_route_renders_preview(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from job_agent.services.recipe_preview_service import PreviewJob, RecipePreviewResult

    def fake_preview(recipe_path, input_value, base_url, rendered, static, root):
        assert recipe_path == "sources/recipes/experimental/eursap-jobs.yaml"
        assert input_value == "output/recipe-calibration/page.html"
        assert base_url == "https://eursap.eu/jobs"
        assert rendered is False
        assert static is True
        return RecipePreviewResult(
            recipe_source_name="Eursap Jobs (experimental)",
            recipe_path=recipe_path,
            recipe_status="experimental",
            input_type="local artifact",
            input_value=input_value,
            base_url=base_url,
            mode_used="local_fixture_html",
            extracted_job_count=1,
            useful_titles=1,
            generic_labels=0,
            unique_urls=1,
            average_description_length=120,
            jobs=[
                PreviewJob(
                    title="SAP Basis Consultant",
                    url="https://eursap.eu/jobs/sap-basis-consultant-34235-remote",
                    location="Remote Work",
                    remote="Not listed",
                    rate="Market Rate",
                    workload="Contract",
                    posted_date="Not listed",
                    start_date="Sep 01, 2026",
                    languages=["English"],
                    description_preview="SAP Basis role preview.",
                    extraction_notes=["Recipe extracted job ID: 34235"],
                )
            ],
            warnings=[],
        )

    monkeypatch.setattr("job_agent.web.routers.recipe_preview.preview_recipe", fake_preview)

    response = client.post(
        "/recipe-preview",
        data={
            "recipe_path": "sources/recipes/experimental/eursap-jobs.yaml",
            "input_path_or_url": "output/recipe-calibration/page.html",
            "base_url": "https://eursap.eu/jobs",
            "mode": "static",
            "source_id": "eursap-jobs",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Eursap Jobs (experimental)" in response.text
    assert "SAP Basis Consultant" in response.text
    assert "Remote Work" in response.text
    assert "Recipe extracted job ID: 34235" in response.text
    assert "Source health saved" in response.text


def test_source_overview_and_detail_routes_render(client: TestClient) -> None:
    overview = client.get("/sources")

    assert overview.status_code == 200
    assert "Extraction health" in overview.text
    assert "Source value" in overview.text
    assert "Manual Intake" in overview.text
    assert "Eursap Jobs" in overview.text
    assert "Whitehall Resources SAP Contract Jobs" in overview.text
    assert "Montreal Associates Job Search" in overview.text

    detail = client.get("/sources/eursap-jobs")

    assert detail.status_code == 200
    assert "sources/recipes/experimental/eursap-jobs.yaml" in detail.text
    assert "live-calibrated experimental" in detail.text
    assert "Extraction health is based on manual recipe preview/test results" in detail.text
    assert "Source value is based on saved job packages and review statuses" in detail.text
    assert "Daily-run Execution" in detail.text
    assert "Create disabled execution entry" in detail.text
    assert "No run data yet" in detail.text
    assert "/recipe-preview" in detail.text
    assert "source_id=eursap-jobs" in detail.text
    assert "Contact tracking not implemented yet" in detail.text


def test_source_routes_render_saved_health(client: TestClient, project_root: Path) -> None:
    from job_agent.services.recipe_preview_service import RecipePreviewResult
    from job_agent.services.source_health_service import SourceHealthService

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
            extracted_job_count=9,
            useful_titles=9,
            generic_labels=0,
            unique_urls=9,
            average_description_length=177,
            jobs=[],
            warnings=[],
        ),
    )

    overview = client.get("/sources")
    detail = client.get("/sources/eursap-jobs")

    assert overview.status_code == 200
    assert "good" in overview.text
    assert "9 jobs extracted, 9 useful titles, no generic labels." in overview.text
    assert detail.status_code == 200
    assert "Recipe Health" in detail.text
    assert "output/recipe-calibration/eursap/page.html" in detail.text
    assert "9 jobs extracted, 9 useful titles, no generic labels." in detail.text


def test_source_routes_render_value_metrics(client: TestClient, project_root: Path) -> None:
    import json

    package_dir = project_root / "output" / "2026-05-09" / "run-1-pkg"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "index.json").write_text(
        json.dumps(
            {
                "stable_id": "job-1",
                "run_id": "run-1",
                "title": "SAP ABAP Consultant",
                "source": "Sample Jobs",
                "source_url": "https://example.com/job-1",
                "match_score": 84,
                "match_category": "strong",
                "application_status": "applied",
            }
        ),
        encoding="utf-8",
    )

    overview = client.get("/sources")
    detail = client.get("/sources/sample-jobs")

    assert overview.status_code == 200
    assert "promising" in overview.text
    assert "Avg/best score: 84/84" in overview.text
    assert detail.status_code == 200
    assert "Value status" in detail.text
    assert "SAP ABAP Consultant" in detail.text
    assert "Last seen run" in detail.text


def test_viewing_source_pages_does_not_mutate_execution_config(client: TestClient, project_root: Path) -> None:
    source_config = project_root / "sources" / "recruiting-sites.yaml"
    source_config.write_text(
        "sources:\n"
        "  - name: Local Sample\n"
        "    type: local_yaml\n"
        "    path: jobs/raw/sample_jobs.yaml\n",
        encoding="utf-8",
    )
    before = source_config.read_text(encoding="utf-8")

    assert client.get("/sources").status_code == 200
    assert client.get("/sources/eursap-jobs").status_code == 200

    assert source_config.read_text(encoding="utf-8") == before


def test_source_execution_routes_create_guard_enable_and_disable(client: TestClient, project_root: Path) -> None:
    from job_agent.io.yaml_store import read_yaml
    from job_agent.services.recipe_preview_service import RecipePreviewResult
    from job_agent.services.source_dry_run_service import DryRunJobPreview, SourceDryRunResult
    from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
    from job_agent.services.source_health_service import SourceHealthService

    create_response = client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)

    assert create_response.status_code == 303
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    entry = config["sources"][0]
    assert entry["source_id"] == "eursap-jobs"
    assert entry["type"] == "recipe_html"
    assert entry["enabled"] is False

    blocked_response = client.post("/sources/eursap-jobs/execution/enable", follow_redirects=False)

    assert blocked_response.status_code == 303
    assert "warning=" in blocked_response.headers["location"]
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    assert config["sources"][0]["enabled"] is False

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
            extracted_job_count=9,
            useful_titles=9,
            generic_labels=0,
            unique_urls=9,
            average_description_length=177,
            jobs=[],
            warnings=[],
        ),
    )
    SourceExecutionReadinessService(project_root).save_from_dry_run(
        SourceDryRunResult(
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
    )

    enable_response = client.post("/sources/eursap-jobs/execution/enable", follow_redirects=False)

    assert enable_response.status_code == 303
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    assert config["sources"][0]["enabled"] is True

    update_response = client.post("/sources/eursap-jobs/execution/update", follow_redirects=False)

    assert update_response.status_code == 303
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    assert len(config["sources"]) == 1
    assert config["sources"][0]["enabled"] is False

    client.post("/sources/eursap-jobs/execution/enable", follow_redirects=False)
    disable_response = client.post("/sources/eursap-jobs/execution/disable", follow_redirects=False)

    assert disable_response.status_code == 303
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    assert config["sources"][0]["enabled"] is False


def test_source_detail_shows_dry_run_link_when_execution_entry_exists(client: TestClient) -> None:
    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)

    detail = client.get("/sources/eursap-jobs")

    assert detail.status_code == 200
    assert "/sources/eursap-jobs/dry-run" in detail.text
    assert "Dry run execution source" in detail.text


def test_source_dry_run_route_renders_result(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from job_agent.services.source_dry_run_service import DryRunJobPreview, SourceDryRunResult

    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)

    class FakeDryRunService:
        def __init__(self, root):
            pass

        def dry_run(self, source_id, *, force_disabled=False):
            assert source_id == "eursap-jobs"
            assert force_disabled is True
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
                        location="Remote",
                        extraction_notes=["Recipe-based extraction; verify details manually."],
                    )
                ],
            )

    monkeypatch.setattr("job_agent.web.routers.sources.SourceDryRunService", FakeDryRunService)

    response = client.get("/sources/eursap-jobs/dry-run?force_disabled=true")

    assert response.status_code == 200
    assert "Source Dry Run" in response.text
    assert "SAP Basis Consultant" in response.text
    assert "Forced disabled source execution" in response.text
    assert "No packages, seen state, materials, digests, or run records were written." in response.text


def test_source_detail_run_now_requires_enabled_source(client: TestClient) -> None:
    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)

    response = client.post("/sources/eursap-jobs/run-now", follow_redirects=False)

    assert response.status_code == 303
    assert "warning=" in response.headers["location"]


def test_source_detail_run_now_redirects_to_run_detail(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    from job_agent.io.yaml_store import read_yaml, write_yaml
    from job_agent.services.single_source_run_service import SingleSourceRunResult

    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)
    config_path = project_root / "sources" / "recruiting-sites.yaml"
    config = read_yaml(config_path, {})
    config["sources"][0]["enabled"] = True
    write_yaml(config_path, config)

    class FakeSingleSourceRunService:
        def __init__(self, root):
            pass

        def run(self, source_id):
            assert source_id == "eursap-jobs"
            return SingleSourceRunResult(
                source_id="eursap-jobs",
                source_name="Eursap Jobs",
                source_type="recipe_html",
                status="completed",
                run_id="run-1",
                run_detail_url="/runs/run-1",
            )

    monkeypatch.setattr("job_agent.web.routers.sources.SingleSourceRunService", FakeSingleSourceRunService)

    response = client.post("/sources/eursap-jobs/run-now", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/runs/run-1"


def test_manual_source_cannot_create_recipe_execution_route(client: TestClient) -> None:
    response = client.post("/sources/manual-intake/execution/create", follow_redirects=False)

    assert response.status_code == 400


def test_jobs_row_click_handler_allows_rows_inside_bulk_form(client: TestClient) -> None:
    response = client.get("/jobs")

    assert response.status_code == 200
    assert 'closest("a, button, input, select, textarea, label")' in response.text
    assert 'closest("a, button, input, select, textarea, label, form")' not in response.text
