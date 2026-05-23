from __future__ import annotations

from fastapi.testclient import TestClient

from job_agent.run_store import RunOptions


def test_dashboard_loads(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Overview" in response.text
    assert "Perform daily run" in response.text


def test_setup_loads_friendly_sections(client: TestClient) -> None:
    response = client.get("/setup")

    assert response.status_code == 200
    assert "Worker Profile" in response.text
    assert "CV Reference Upload" in response.text
    assert "Advanced profile files and writing templates" in response.text
    assert "Template variable reference" in response.text
    assert "Highest performance" in response.text
    assert "Minimum digest score" in response.text
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
    assert 'option value="interesting" selected' in response.text
    assert 'option value="exploratory" selected' in response.text


def test_run_options_include_material_generation_flag() -> None:
    options = RunOptions()

    assert not options.generate_materials


def test_ai_edit_context_endpoint(client: TestClient) -> None:
    response = client.get("/api/ai-edit/context", params={"field_id": "profile.skills", "button_id": "setup.skills"})

    assert response.status_code == 200
    data = response.json()
    assert "blocks" in data
    assert "selected_blocks" in data
