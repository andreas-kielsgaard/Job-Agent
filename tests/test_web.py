from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from tests.helpers import write_sample_package

from job_agent.models import Job
from job_agent.run_store import RunEvent, RunOptions, RunStore
from job_agent.services.source_listing_index_store import SourceListingIndexStore


def test_dashboard_loads(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Today" in response.text
    assert "View jobs found today" in response.text
    assert "Source listings are raw rows" in response.text
    assert "Perform daily run" in response.text
    assert "Open setup guide" in response.text
    assert 'id="setup-guide-companion"' in response.text
    assert "job-agent-folio-character-avatar.svg" in response.text


def test_setup_guide_page_and_minimized_companion_state(client: TestClient, project_root) -> None:
    response = client.get("/setup-guide")

    assert response.status_code == 200
    assert "<title>Setup Guide - Job Agent</title>" in response.text
    assert "Get To The First Run" in response.text
    assert "Connect Claude" in response.text
    assert "can be minimized from the agent icon" in response.text
    assert "Hide guide" not in response.text
    assert "profile/setup-guide.json" in response.text
    assert "job-agent-folio-character-transparent.svg" in response.text

    skip = client.post(
        "/api/setup-guide/steps/claude/dismiss",
        data={"return_to": "/setup-guide"},
        follow_redirects=False,
    )
    assert skip.status_code == 303
    assert skip.headers["location"] == "/setup-guide"
    state = (project_root / "profile" / "setup-guide.json").read_text(encoding="utf-8")
    assert '"claude"' in state

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Suggest sources" in dashboard.text

    dismiss = client.post(
        "/api/setup-guide/dismiss",
        data={"return_to": "/"},
        follow_redirects=False,
    )
    assert dismiss.status_code == 303
    assert '"guide_dismissed": false' in (project_root / "profile" / "setup-guide.json").read_text(encoding="utf-8")
    assert 'id="setup-guide-companion"' in client.get("/").text


def test_setup_loads_friendly_sections(client: TestClient) -> None:
    response = client.get("/setup")

    assert response.status_code == 200
    assert "Worker Profile" in response.text
    assert "CV Reference" in response.text
    assert "Upload CV" in response.text
    assert "Enhance profile draft with Claude" in response.text
    assert "Store CV and extract plain text" in response.text
    assert 'name="configure_contact" checked' in response.text
    assert 'name="configure_preferences" checked' in response.text
    assert 'name="configure_match_engine" checked' in response.text
    assert "Professional links" in response.text
    assert "Profile Checklist" not in response.text
    assert "Profile Map" not in response.text
    assert "profile-checklist-panel" not in response.text
    assert "profile-checklist-board" not in response.text
    assert "cv-reference-workspace" in response.text
    assert "profile-map-panel" not in response.text
    assert "Skill Matrix" in response.text
    assert "Writing Style &amp; Examples" in response.text
    assert "AI Review &amp; Writing" in response.text
    assert 'class="setup-outline"' in response.text
    assert 'href="#cv-reference"' in response.text
    assert 'href="#profile-contract"' not in response.text
    assert "Open scoring sandbox" in response.text
    assert 'name="skill_terms"' not in response.text
    assert 'name="module_terms"' not in response.text
    assert 'name="caveat_terms"' not in response.text
    assert "One matching term per line" not in response.text
    assert 'id="tag-editor-dialog"' in response.text
    assert "data-case-linked-skills" not in response.text
    assert 'data-case-link-list="skills"' in response.text
    assert "Advanced profile files and writing templates" in response.text
    assert "Connector setup" in response.text
    assert "Canva" in response.text
    assert "Connect to Canva" in response.text
    assert "/connectors/canva/start" in response.text
    assert "canva_mcp_server_url" not in response.text
    assert "canva_oauth_client_id" not in response.text
    assert "canva_connected_account" not in response.text
    assert "email_oauth_client_id" not in response.text
    assert "Email Drafts" in response.text
    assert "Sending is disabled by the application boundary" in response.text
    assert "Template variable reference" in response.text
    assert "Highest performance" in response.text
    assert "Daily-run inclusion score" in response.text
    assert "Minimum digest score" not in response.text
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
    response = client.get(
        "/jobs?app_status=interesting&app_status=not_interesting&category=strong&category=exploratory"
    )

    assert response.status_code == 200
    assert "Jobs" in response.text
    assert 'data-name="app_status"' in response.text
    assert 'data-value="interesting"' in response.text
    assert 'data-value="exploratory"' in response.text
    assert 'class="tri-filter-option include"' in response.text


def test_jobs_page_renders_dynamic_sortable_table(client: TestClient) -> None:
    response = client.get("/jobs")

    assert response.status_code == 200
    assert 'id="jobs-filter-form"' in response.text
    assert 'data-date-shortcut="today"' in response.text
    assert "initJobDateShortcuts" in response.text
    assert "data-job-selection-toolbar" in response.text
    assert 'data-bulk-status="interesting"' in response.text
    assert "initJobStatusTriage" in response.text
    assert "toggleJobPreview" in response.text
    assert "job-table-before-sort" in response.text
    assert "Apply to selected" not in response.text
    assert "data-sortable-table" in response.text
    assert 'data-sort-key="role"' in response.text
    assert 'data-sort-key="pay"' in response.text
    assert 'data-sort-key="match"' in response.text
    assert 'data-default-sort-key="match"' in response.text
    assert 'data-default-sort-direction="desc"' in response.text
    assert 'data-sort-heading="match" aria-sort="descending"' in response.text
    assert 'data-sort-initial="desc">Match' in response.text
    assert "resizeJobTableShell" in response.text
    assert "scheduleJobTableShellResize" in response.text
    assert 'window.addEventListener("scroll"' in response.text
    assert "initJobTableSorting" in response.text


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
            "rate": "DKK 650/day",
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
            "rate": "EUR 900/day",
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
    assert "DKK 650/day" in response.text
    assert 'href="/jobs/weak-1?run_id=run-1"' in response.text
    assert "Open detail in new tab" in response.text
    assert "Click a row for a preview" in response.text
    assert "job-detail-preview-row" in response.text
    assert "job-preview-description" in response.text
    assert "Posting Text" in response.text
    assert "data-job-row" in response.text
    assert "job-status-control" in response.text
    assert "job-status-display" in response.text
    assert 'data-row-status="interesting"' in response.text
    assert "job-hover-card" not in response.text
    assert 'data-sort-pay="650.0"' in response.text
    assert 'data-value="sample-jobs"' in response.text
    assert 'data-value="run-1"' in response.text
    assert 'value="2026-05-20"' in response.text
    assert 'data-value="weak"' in response.text
    assert 'data-value="not_interesting"' in response.text

    default_response = client.get("/jobs?dedupe=0")
    assert default_response.status_code == 200
    assert "Low relevance SAP Role" not in default_response.text
    assert "Strong SAP Role" in default_response.text


def test_jobs_status_json_updates_package_without_page_reload(client: TestClient, project_root) -> None:
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
            "application_status": "unreviewed",
            "material_status": "generated",
        },
    )

    response = client.post("/api/jobs/status", json={"job_ids": ["strong-1"], "status": "applied"})

    assert response.status_code == 200
    assert response.json()["updated_ids"] == ["strong-1"]
    jobs = client.get("/jobs?category=strong&app_status=applied&dedupe=0")
    assert jobs.status_code == 200
    assert "Strong SAP Role" in jobs.text
    assert 'data-current-status="applied"' in jobs.text
    assert '<span class="badge applied" data-application-status-badge>applied</span>' in jobs.text


def test_applied_jobs_use_effective_strong_match_in_jobs_view(client: TestClient, project_root) -> None:
    package = {
        "stable_id": "applied-weak",
        "run_id": "run-1",
        "title": "Applied Low Score Role",
        "source": "Sample Jobs",
        "source_id": "sample-jobs",
        "source_url": "https://example.com/jobs/applied-weak",
        "match_score": 31,
        "match_category": "weak",
        "application_status": "applied",
        "material_status": "generated",
    }
    _write_package(project_root, "2026-05-21", "run-1", "applied-weak", package)

    default_jobs = client.get("/jobs?dedupe=0")
    strong_jobs = client.get("/jobs?category=strong&app_status=applied&dedupe=0")

    assert default_jobs.status_code == 200
    assert strong_jobs.status_code == 200
    assert "Applied Low Score Role" in default_jobs.text
    assert "Applied Low Score Role" in strong_jobs.text
    assert '<span class="badge strong">100% strong</span>' in strong_jobs.text
    saved = json.loads((project_root / "output" / "2026-05-21" / "applied-weak" / "index.json").read_text())
    assert saved["match_score"] == 31
    assert saved["match_category"] == "weak"


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


def test_run_status_payload_includes_live_view_sections(client: TestClient, project_root) -> None:
    store = RunStore(project_root)
    record = store.create_run(RunOptions())
    store.update(record.run_id, status="running")
    store.append_event(
        RunEvent(
            record.run_id,
            "source_started",
            "Checking source 1/1: Local",
            phase="source_ingestion",
            current_source="Local",
            counts={"source_index": 1, "source_count": 1},
        )
    )
    store.append_event(
        RunEvent(
            record.run_id,
            "source_activity",
            "Pagination page read",
            phase="source_ingestion",
            current_source="Local",
            counts={
                "source_index": 1,
                "source_count": 1,
                "jobs_found": 12,
                "page_explored_count": 2,
                "page_total": 5,
                "visible_total_job_count": 30,
            },
        )
    )
    store.append_event(
        RunEvent(
            record.run_id,
            "match_highlight",
            "Highlighted match: SAP ABAP Consultant - 90% - strong match category",
            phase="scoring",
            current_source="Local",
            current_job="SAP ABAP Consultant",
            counts={"score": 90, "source_index": 1, "source_count": 1},
        )
    )
    store.append_event(
        RunEvent(
            record.run_id,
            "match_highlight",
            "Highlighted match: SAP Basis Consultant - 88% - strong match category",
            phase="scoring",
            current_source="Local",
            current_job="SAP Basis Consultant",
            counts={"score": 88, "source_index": 1, "source_count": 1},
        )
    )

    response = client.get(f"/api/runs/{record.run_id}/status")

    assert response.status_code == 200
    data = response.json()
    assert data["run_overview"]["is_running"] is True
    assert data["run_progress"]["is_running"] is True
    assert data["run_progress"]["running_sources"] == 1
    assert data["source_progress"]["summary"]["total_sources"] == 1
    assert data["source_progress"]["items"][0]["source_name"] == "Local"
    assert data["source_progress"]["items"][0]["is_running"] is True
    assert data["source_progress"]["items"][0]["listing_progress_text"] == "2/5 pages"
    assert data["source_progress"]["items"][0]["coverage_text"] == "12/30"
    assert "source_results_html" in data
    assert 'id="source-results-panel"' in data["source_results_html"]
    assert 'data-source-run-card="1"' in data["source_results_html"]
    assert "2/5 pages" in data["source_results_html"]
    assert data["source_progress"]["items"][0]["highlights"][0]["title"] == "SAP ABAP Consultant"
    assert data["source_progress"]["items"][0]["highlights"][1]["title"] == "SAP Basis Consultant"
    assert data["match_highlights"][0]["current_job"] == "SAP ABAP Consultant"
    assert "packages" in data


def test_running_run_detail_uses_live_status_polling(client: TestClient, project_root) -> None:
    store = RunStore(project_root)
    record = store.create_run(RunOptions())
    store.update(record.run_id, status="running")

    response = client.get(f"/runs/{record.run_id}")

    assert response.status_code == 200
    assert "/api/runs/" in response.text
    assert "refreshRunDetail" in response.text
    assert "setTimeout(() => location.reload(), 2500)" not in response.text


def test_completed_run_status_does_not_show_unfinished_sources_as_running(client: TestClient, project_root) -> None:
    store = RunStore(project_root)
    record = store.create_run(RunOptions())
    store.update(record.run_id, status="completed")
    store.append_event(
        RunEvent(
            record.run_id,
            "source_started",
            "Checking source 1/2: First",
            phase="source_ingestion",
            current_source="First",
            counts={"source_index": 1, "source_count": 2},
        )
    )
    store.append_event(
        RunEvent(
            record.run_id,
            "source_completed",
            "Completed source 1/2: First - 2 jobs found, 0 warnings",
            phase="source_ingestion",
            current_source="First",
            counts={"source_index": 1, "source_count": 2, "jobs_found": 2},
        )
    )
    store.append_event(
        RunEvent(
            record.run_id,
            "source_started",
            "Checking source 2/2: Second",
            phase="source_ingestion",
            current_source="Second",
            counts={"source_index": 2, "source_count": 2},
        )
    )

    data = client.get(f"/api/runs/{record.run_id}/status").json()

    assert data["run_progress"]["running_sources"] == 0
    assert data["run_progress"]["is_running"] is False
    assert data["run_progress"]["finished_sources"] == 2
    assert data["source_progress"]["items"][1]["status"] == "deferred"
    assert data["source_progress"]["items"][1]["is_running"] is False
    assert data["source_progress"]["items"][1]["highlight"]["title"] == "Left for another pass"


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
    assert any(block["key"] == "application_examples" for block in data["blocks"])


def test_ai_edit_external_agent_mode_returns_prompt(client: TestClient, project_root) -> None:
    response = client.post(
        "/api/ai-edit/generate",
        json={
            "mode": "external_agent",
            "field_id": "profile.canonical_cv",
            "button_id": "setup.canonical_cv",
            "current_text": "Current CV",
            "user_instruction": "Tighten the language.",
            "selected_blocks": ["app_context", "canonical_cv"],
            "disabled_blocks": [],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["interaction_id"]
    assert "Tighten the language" in data["prompt"]
    assert (project_root / "output" / "external-agent-interactions").exists()


def test_job_review_bundle_uses_external_agent_controller(client: TestClient, project_root) -> None:
    write_sample_package(project_root)

    detail = client.get("/jobs/stable-1?run_id=run-1")
    response = client.post(
        "/api/jobs/stable-1/review-bundle/external-agent/prepare",
        data={"run_id": "run-1"},
    )

    assert detail.status_code == 200
    assert "Open detail in new tab" not in detail.text
    assert "Open one-page CV" not in detail.text
    assert "Preview PDF" in detail.text
    assert "/jobs/stable-1/files/focused_cv_pdf?run_id=run-1" in detail.text
    assert "/jobs/stable-1/files/focused_cv_tex?run_id=run-1&amp;download=1" in detail.text
    assert "View posting on external website" in detail.text
    assert "Quick status" not in detail.text
    assert 'data-status-choice="interesting"' in detail.text
    assert 'data-status-choice="not_interesting"' in detail.text
    assert 'data-status-choice="applied"' in detail.text
    assert "Decision notes for this job" in detail.text
    assert "status-modal" in detail.text
    assert "Open source link" not in detail.text
    assert "View saved posting snapshot" in detail.text
    assert "/jobs/stable-1/files/posting_snapshot?run_id=run-1" in detail.text
    assert 'href="https://example.com/stable-1"' in detail.text
    assert "List Snapshot" not in detail.text
    assert "What this job actually says" in detail.text
    assert "What the generator creates" in detail.text
    assert "LaTeX source plus a PDF preview/download" in detail.text
    assert "Prepare AI review bundle" in detail.text
    assert "/review-bundle/external-agent/prepare" in detail.text
    assert 'id="review-bundle"' not in detail.text
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["response_mode"] == "none"
    assert "External Agent Review Bundle" in data["prompt"]

    cv_file = client.get("/jobs/stable-1/files/focused_cv_pdf?run_id=run-1")
    assert cv_file.status_code == 200
    assert cv_file.headers["content-type"].startswith("application/pdf")

    tex_file = client.get("/jobs/stable-1/files/focused_cv_tex?run_id=run-1&download=1")
    assert tex_file.status_code == 200
    assert tex_file.headers["content-type"].startswith("text/x-tex")

    snapshot_file = client.get("/jobs/stable-1/files/posting_snapshot?run_id=run-1")
    assert snapshot_file.status_code == 200
    assert snapshot_file.headers["content-type"].startswith("text/markdown")
    assert "Saved Posting Snapshot" in snapshot_file.text


def test_job_detail_renders_extracted_tag_groups(client: TestClient, project_root) -> None:
    paths = write_sample_package(project_root)
    job_path = Path(paths["job"])
    job_payload = json.loads(job_path.read_text(encoding="utf-8"))
    job_payload["required_skills"] = ["SAP PS", "ABAP"]
    job_payload["required_modules"] = ["PS"]
    job_payload["required_languages"] = ["English"]
    job_path.write_text(json.dumps(job_payload), encoding="utf-8")

    detail = client.get("/jobs/stable-1?run_id=run-1")

    assert detail.status_code == 200
    assert "Required skills" in detail.text
    assert "SAP PS" in detail.text
    assert "Required modules" in detail.text
    assert "Languages" in detail.text


def test_job_detail_status_save_redirects_with_confirmation(client: TestClient, project_root) -> None:
    write_sample_package(project_root)

    response = client.post(
        "/api/jobs/stable-1/status",
        data={
            "status": "not_interesting",
            "not_interesting_reason": "Rate too low",
            "notes": "Keep recruiter in mind.",
            "return_to": "/jobs/stable-1?run_id=run-1#status",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/jobs/stable-1?run_id=run-1&status_saved=not_interesting#status"

    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert "Status saved as <strong>not interesting</strong>" in detail.text
    assert "Saved as not interesting." in detail.text
    assert "Rate too low" in detail.text
    assert "Keep recruiter in mind." in detail.text


def test_job_detail_copy_context_includes_job_app_and_profile_context(client: TestClient, project_root) -> None:
    write_sample_package(project_root)

    detail = client.get("/jobs/stable-1?run_id=run-1")
    response = client.post("/api/jobs/stable-1/context/copy", data={"run_id": "run-1"})

    assert detail.status_code == 200
    assert "Copy complete job context" in detail.text
    assert "/api/jobs/stable-1/context/copy" in detail.text
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    context = data["context"]
    assert "Job Agent Copy Context" in context
    assert "local-first job preparation application" in context
    assert "SAP ABAP Consultant" in context
    assert "## Job JSON" in context
    assert "## Match JSON" in context
    assert "Canonical CV text" in context
    assert "Direct consultant tone." in context
    assert "Generated Materials" in context
