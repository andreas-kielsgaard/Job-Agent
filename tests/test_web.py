from __future__ import annotations

import json

from fastapi.testclient import TestClient

from job_agent.models import Job
from job_agent.run_store import RunEvent, RunOptions, RunStore
from job_agent.services.source_listing_index_store import SourceListingIndexStore


def test_dashboard_loads(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Overview" in response.text
    assert "Perform daily run" in response.text


def test_setup_loads_friendly_sections(client: TestClient) -> None:
    response = client.get("/setup")

    assert response.status_code == 200
    assert "Worker Profile" in response.text
    assert "CV Reference" in response.text
    assert "Upload or Replace CV" in response.text
    assert "Profile Map" in response.text
    assert "profile-checklist-panel" in response.text
    assert "profile-checklist-board" in response.text
    assert "cv-reference-dashboard" in response.text
    assert "cv-reference-workspace" in response.text
    assert "profile-map-panel" in response.text
    assert "Use this map to see where profile information lives" in response.text
    assert "Core:" in response.text
    assert "Basics / Preferences / Profile signals" in response.text
    assert "Skill matrix and caveats" in response.text
    assert 'class="setup-outline"' in response.text
    assert 'href="#cv-reference"' in response.text
    assert 'href="#profile-contract"' in response.text
    assert "Open scoring sandbox" in response.text
    assert "Advanced profile files and writing templates" in response.text
    assert "Template variable reference" in response.text
    assert "Highest performance" in response.text
    assert "Minimum digest score" in response.text
    assert response.text.index('id="profile-checklist"') < response.text.index('id="cv-reference"')
    assert "Job Sources" not in response.text
    assert "Manage sources" not in response.text
    assert "Add source" not in response.text
    assert "Add Simple Source" not in response.text


def test_dashboard_has_material_generation_option(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Generate materials during run" in response.text
    assert "Usually leave this off" in response.text


def test_jobs_and_stats_pages_load(client: TestClient) -> None:
    assert client.get("/jobs").status_code == 200
    stats = client.get("/stats")
    assert stats.status_code == 200
    assert "Stats" in stats.text
    assert client.get("/runs?view=test").status_code == 200
    assert client.get("/runs?view=archived").status_code == 200
    assert client.get("/runs?view=deleted").status_code == 200


def test_jobs_multi_filters_load(client: TestClient) -> None:
    response = client.get("/jobs?app_status=interesting&app_status=not_interesting&category=strong&category=exploratory")

    assert response.status_code == 200
    assert "Jobs" in response.text
    assert 'data-name="app_status"' in response.text
    assert 'data-value="interesting"' in response.text
    assert 'data-value="exploratory"' in response.text
    assert 'class="tri-filter-option include"' in response.text


def test_jobs_filters_by_source_run_date_and_low_relevance(client: TestClient, project_root) -> None:
    _write_package(
        project_root,
        "2026-05-20",
        "run-1",
        "weak-1",
        {
            "stable_id": "weak-1",
            "run_id": "run-1",
            "title": "Low relevance SAP Role",
            "source": "Sample Jobs",
            "source_id": "sample-jobs",
            "source_url": "https://example.com/jobs/weak",
            "match_score": 31,
            "match_category": "weak",
            "application_status": "not_interesting",
            "material_status": "missing",
        },
    )
    _write_package(
        project_root,
        "2026-05-21",
        "run-2",
        "strong-1",
        {
            "stable_id": "strong-1",
            "run_id": "run-2",
            "title": "Strong SAP Role",
            "source": "Other",
            "source_id": "other-source",
            "source_url": "https://other.example.com/jobs/strong",
            "match_score": 88,
            "match_category": "strong",
            "application_status": "interesting",
            "material_status": "generated",
        },
    )

    response = client.get(
        "/jobs?source_id=sample-jobs&run_id=run-1&date_from=2026-05-20&date_to=2026-05-20"
        "&category=weak&app_status=not_interesting&material_status=missing&dedupe=0"
    )

    assert response.status_code == 200
    assert "Low relevance SAP Role" in response.text
    assert "Strong SAP Role" not in response.text
    assert 'data-value="sample-jobs"' in response.text
    assert 'data-value="run-1"' in response.text
    assert 'value="2026-05-20"' in response.text
    assert 'data-value="weak"' in response.text
    assert 'data-value="not_interesting"' in response.text

    default_response = client.get("/jobs?dedupe=0")
    assert default_response.status_code == 200
    assert "Low relevance SAP Role" not in default_response.text
    assert "Strong SAP Role" in default_response.text


def test_jobs_view_includes_indexed_listing_rows(client: TestClient, project_root) -> None:
    SourceListingIndexStore(project_root).record_index(
        source_id="sample-jobs",
        source_name="Sample Jobs",
        jobs=[
            Job(
                title="Indexed SAP Listing",
                source="Sample Jobs",
                source_id="sample-jobs",
                url="https://example.com/jobs/indexed",
            )
        ],
    )

    response = client.get("/jobs?source_id=sample-jobs&category=not_scored&dedupe=0")

    assert response.status_code == 200
    assert "Indexed SAP Listing" in response.text
    assert "indexed only" in response.text
    assert "Indexed listing" in response.text


def test_run_options_include_material_generation_flag() -> None:
    options = RunOptions()

    assert not options.generate_materials


def test_run_status_log_and_lifecycle_routes_use_executor_boundary(client: TestClient, project_root) -> None:
    store = RunStore(project_root)
    record = store.create_run(RunOptions())
    store.update(record.run_id, status="running")
    store.append_event(RunEvent(record.run_id, "sample", "Sample run event.", phase="test"))

    status = client.get(f"/api/runs/{record.run_id}/status")
    log = client.get(f"/api/runs/{record.run_id}/log")
    archive = client.post(
        "/api/runs/bulk",
        data={"run_ids": [record.run_id, "missing-run"], "action": "archive"},
        follow_redirects=False,
    )
    restore = client.post(f"/api/runs/{record.run_id}/restore", follow_redirects=False)
    delete = client.post(f"/api/runs/{record.run_id}/delete", follow_redirects=False)

    assert status.status_code == 200
    assert status.json()["latest_event"]["message"] == "Sample run event."
    assert log.status_code == 200
    assert "Sample run event." in log.text
    assert archive.status_code == 303
    assert restore.status_code == 303
    assert delete.status_code == 303
    assert RunStore(project_root).get(record.run_id).visibility == "deleted"


def _write_package(project_root, run_date: str, run_id: str, package_id: str, data: dict) -> None:
    package_dir = project_root / "output" / run_date / package_id
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "index.json").write_text(json.dumps(data), encoding="utf-8")


def test_ai_edit_context_endpoint(client: TestClient) -> None:
    response = client.get("/api/ai-edit/context", params={"field_id": "profile.skills", "button_id": "setup.skills"})

    assert response.status_code == 200
    data = response.json()
    assert "blocks" in data
    assert "selected_blocks" in data
