from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_agent.run_store import RunEvent, RunOptions, RunStore
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


def test_app_creation_health_and_basic_routes_use_temp_root(client: TestClient, project_root: Path) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    for path in ["/", "/runs", "/jobs", "/stats", "/setup"]:
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
