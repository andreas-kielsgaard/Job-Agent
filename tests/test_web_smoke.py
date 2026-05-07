from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
