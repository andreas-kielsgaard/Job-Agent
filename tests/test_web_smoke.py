from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.helpers import seed_common_sources

from job_agent.run_store import RunEvent, RunOptions, RunStore
from job_agent.services.cv_profile_draft_service import CvProfileDraftService

_SOURCE_FIXTURE_TESTS = {
    "test_source_test_route_replaces_configured_recipe_review",
    "test_source_detail_shows_latest_source_test_evidence_without_preview_route",
    "test_source_overview_and_detail_routes_render",
    "test_source_session_route_records_storage_state",
    "test_source_session_capture_route_starts_guided_browser_capture",
    "test_source_detail_updates_selected_recipe",
    "test_source_detail_updates_registry_fields",
    "test_source_archive_hides_source_from_default_overview_and_disables_execution",
    "test_source_detail_capture_calibration_action_is_bounded",
    "test_source_routes_render_saved_health",
    "test_source_detail_safe_test_panel_uses_session_cta_when_access_blocked",
    "test_source_routes_render_value_metrics",
    "test_viewing_source_pages_does_not_mutate_execution_config",
    "test_source_execution_routes_create_guard_enable_and_disable",
    "test_source_detail_shows_dry_run_link_when_execution_entry_exists",
    "test_source_test_run_view_and_api_save_readiness",
    "test_source_test_insight_can_rebuild_reading_plan_with_clues",
    "test_source_test_insight_prioritizes_failed_source_access",
    "test_rebuild_from_test_redirects_to_retest_when_plan_changed",
    "test_source_detail_run_now_redirects_to_run_detail",
    "test_source_detail_index_and_investigate_actions_redirect",
    "test_source_detail_index_and_investigate_actions_require_ready_source",
}


@pytest.fixture(autouse=True)
def seed_sources_for_configured_source_smoke_tests(request: pytest.FixtureRequest, project_root: Path) -> None:
    if request.node.name in _SOURCE_FIXTURE_TESTS:
        seed_common_sources(project_root)


def test_app_creation_health_and_basic_routes_use_temp_root(client: TestClient, project_root: Path) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    work_status = client.get("/api/work-status")
    assert work_status.status_code == 200
    assert work_status.json()["sources"] == []

    for path in [
        "/",
        "/runs",
        "/jobs",
        "/applications",
        "/stats",
        "/setup",
        "/match-sandbox",
        "/postings/new",
        "/compatibility",
        "/sources",
    ]:
        assert client.get(path).status_code == 200

    assert (project_root / "output" / "runs" / "runs.json").exists()
    dashboard = client.get("/").text
    assert 'id="work-status-dock"' in dashboard
    assert "Claude not connected for material generation" in dashboard
    assert "Ingest all ready sources" in dashboard
    assert 'name="use_llm"' not in dashboard
    material_checkbox = re.search(r'<input[^>]+name="generate_materials_option"[^>]*>', dashboard)
    assert material_checkbox
    assert "checked" not in material_checkbox.group(0)

    posting = client.get("/postings/new").text
    assert "Claude not connected for AI-enhanced evaluation" in posting
    assert "Claude not connected for material generation" in posting


def test_browser_tab_titles_describe_current_page(client: TestClient, project_root: Path) -> None:
    seed_common_sources(project_root)
    cases = [
        ("/", "Dashboard"),
        ("/jobs", "Jobs"),
        ("/applications", "Applications"),
        ("/runs?view=test", "Test Runs"),
        ("/setup", "Setup"),
        ("/sources", "Sources"),
        ("/sources/new", "Add Source"),
        ("/sources/suggest", "Suggest Sources"),
        ("/sources/eursap-jobs", "Source - Eursap Jobs"),
        ("/sources/eursap-jobs/session", "Source Session - Eursap Jobs"),
        ("/sources/eursap-jobs/test-run", "Source Test - Eursap Jobs"),
        ("/compatibility?source_mode=configured&selected_source_id=eursap-jobs", "Compatibility - Eursap Jobs"),
        ("/postings/new", "Add Posting"),
    ]
    for path, title in cases:
        response = client.get(path)

        assert response.status_code == 200
        assert f"<title>{title} - Job Agent</title>" in response.text


def test_app_icons_are_linked_and_served(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'rel="icon" type="image/svg+xml" href="/static/icons/job-agent-icon-minimal-autumn.svg' in response.text
    assert 'rel="alternate icon" href="/static/icons/job-agent-icon-minimal-autumn.ico' in response.text
    assert 'rel="apple-touch-icon" href="/static/icons/job-agent-icon-minimal-autumn-256.png' in response.text
    assert 'class="brand-mark"' in response.text
    assert "/static/icons/job-agent-icon-minimal-autumn.svg" in response.text

    for path in [
        "/static/icons/job-agent-icon-minimal-autumn.svg",
        "/static/icons/job-agent-icon-minimal-autumn.ico",
        "/static/icons/job-agent-icon-minimal-autumn-256.png",
    ]:
        assert client.get(path).status_code == 200


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

    old_source_add = client.post(
        "/setup/source-add",
        data={"name": "Bad", "source_type": "generic_html", "url_or_path": ""},
        follow_redirects=False,
    )
    assert old_source_add.status_code == 303
    assert old_source_add.headers["location"].startswith("/sources/new")

    old_source_toggle = client.post(
        "/setup/source-toggle",
        data={"index": "0", "enabled": "1"},
        follow_redirects=False,
    )
    assert old_source_toggle.status_code == 303
    assert old_source_toggle.headers["location"].startswith("/sources")

    source_config = project_root / "sources" / "recruiting-sites.yaml"
    before = source_config.read_text(encoding="utf-8") if source_config.exists() else ""
    redirected_source_add = client.post(
        "/setup/source-add",
        data={"name": "Local", "source_type": "local_yaml", "url_or_path": "jobs/raw/manual.yaml"},
        follow_redirects=False,
    )
    assert redirected_source_add.status_code == 303
    after = source_config.read_text(encoding="utf-8") if source_config.exists() else ""
    assert after == before

    match_response = client.post(
        "/setup/match-engine",
        data={
            "remote_policy": "required",
            "permanent_policy": "exclude",
            "permanent_penalty": "-30",
            "technical_cap": "70",
            "module_cap": "25",
            "technical_rule_label": ["ABAP variants"],
            "technical_rule_terms": ["abap\nsap abap"],
            "technical_rule_score": ["40"],
            "technical_rule_mode": ["required"],
            "module_rule_label": ["QM"],
            "module_rule_terms": ["qm"],
            "module_rule_score": ["7"],
            "module_rule_mode": ["bonus"],
            "contract_rule_label": ["Contract"],
            "contract_rule_terms": ["contract"],
            "contract_rule_score": ["8"],
            "contract_rule_mode": ["bonus"],
        },
        follow_redirects=False,
    )
    assert match_response.status_code == 303
    assert "remote_policy: required" in (project_root / "profile" / "preferences.yaml").read_text(encoding="utf-8")

    run_inclusion = client.post(
        "/setup/run-inclusion",
        data={"minimum_digest_score": "55"},
        follow_redirects=False,
    )
    assert run_inclusion.status_code == 303
    assert "minimum_digest_score: 55" in (project_root / "profile" / "preferences.yaml").read_text(encoding="utf-8")
    preferences_before_skills = (project_root / "profile" / "preferences.yaml").read_text(encoding="utf-8")

    skills_response = client.post(
        "/setup/skills",
        data={
            "skill_name": ["SAP ABAP"],
            "module_lane": ["strong"],
            "module_name": ["QM"],
            "role_bucket": ["high_match"],
            "role_name": ["SAP Developer"],
            "caveat_key": ["fiori"],
            "caveat_text": ["Backend Fiori caveat."],
        },
        follow_redirects=False,
    )
    assert skills_response.status_code == 303
    assert "SAP Developer" in (project_root / "profile" / "skills.yaml").read_text(encoding="utf-8")
    assert (project_root / "profile" / "preferences.yaml").read_text(encoding="utf-8") == preferences_before_skills

    cases_response = client.post(
        "/setup/case-studies",
        data={
            "case_company": ["LEGO"],
            "case_role": ["Developer"],
            "case_highlights": ["Built service"],
            "case_keywords": ["ABAP"],
            "case_linked_skills": ["SAP ABAP"],
            "case_linked_modules": ["QM"],
            "case_linked_roles": ["SAP Developer"],
        },
        follow_redirects=False,
    )
    assert cases_response.status_code == 303
    assert "linked_skills" in (project_root / "profile" / "experience.yaml").read_text(encoding="utf-8")

    examples_response = client.post(
        "/setup/application-examples",
        data={
            "example_id": [""],
            "example_label": ["ABAP note"],
            "example_application_text": ["Human edited text"],
            "example_job_title": ["ABAP Consultant"],
            "example_company": ["Recruiter"],
            "example_url": ["https://example.com"],
            "example_linked_skills": ["SAP ABAP"],
            "example_linked_modules": ["QM"],
            "example_linked_roles": ["SAP Developer"],
            "example_notes": ["Good tone"],
        },
        follow_redirects=False,
    )
    assert examples_response.status_code == 303
    assert "Human edited text" in (project_root / "profile" / "application-examples.yaml").read_text(encoding="utf-8")

    policy_response = client.post(
        "/setup/ai-policy",
        data={
            "ai_min_score": "40",
            "evaluate_category": ["strong", "exploratory"],
            "trigger_on_review_triggers": "on",
            "acceptable_languages": "English",
            "fluent_languages": "Danish",
            "language_penalty": "-20",
            "core_match_groups": "ABAP",
            "min_core_matches": "2",
            "high_rate_threshold": "900",
        },
        follow_redirects=False,
    )
    assert policy_response.status_code == 303
    assert "ai_review_policy" in (project_root / "profile" / "preferences.yaml").read_text(encoding="utf-8")

    (project_root / ".env").write_text(
        "CANVA_CLIENT_ID=canva-client\nCANVA_CLIENT_SECRET=canva-secret\n",
        encoding="utf-8",
    )
    canva_start = client.post("/connectors/canva/start", follow_redirects=False)
    assert canva_start.status_code == 303
    assert canva_start.headers["location"].startswith("https://www.canva.com/api/oauth/authorize?")
    assert "client_id=canva-client" in canva_start.headers["location"]
    connectors_yaml = (project_root / "connectors.yaml").read_text(encoding="utf-8")
    assert "pending_oauth" in connectors_yaml
    assert "code_verifier" in connectors_yaml

    connector_response = client.post(
        "/setup/connectors",
        data={
            "email_enabled": "on",
            "email_provider": "gmail",
            "email_mode": "draft_only",
        },
        follow_redirects=False,
    )
    assert connector_response.status_code == 303
    connectors_yaml = (project_root / "connectors.yaml").read_text(encoding="utf-8")
    assert "pending_oauth" in connectors_yaml
    assert "sending_enabled: false" in connectors_yaml

    writing_response = client.post(
        "/setup/writing-reference",
        data={"writing_style": "Concise human-edited tone."},
        follow_redirects=False,
    )
    assert writing_response.status_code == 303
    assert "Concise human-edited tone" in (project_root / "profile" / "writing-style.md").read_text(encoding="utf-8")


def test_unsupported_cv_upload_suffix_returns_400(client: TestClient) -> None:
    response = client.post(
        "/setup/cv-reference",
        files={"cv_file": ("cv.exe", b"not a cv", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_cv_upload_can_auto_configure_selected_profile_sections(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    class FakeLlmService:
        def __init__(self, root):
            pass

        def is_configured(self):
            return True

        def complete(self, prompt, **kwargs):
            assert "Requested sections: canonical_cv, match_engine" in prompt
            return type(
                "Completion",
                (),
                {
                    "text": (
                        '{"canonical_cv":"# CV\\nABAP consultant",'
                        '"match_engine":{"remote_policy":"required","permanent_policy":"exclude",'
                        '"technical_keyword_groups":[{"label":"ABAP variants",'
                        '"terms":["abap","abap coding"],"score":40,"mode":"required"}]}}'
                    )
                },
            )()

    monkeypatch.setattr("job_agent.services.setup_service.LlmService", FakeLlmService)

    response = client.post(
        "/setup/cv-reference",
        data={
            "auto_configure_profile": "on",
            "configure_canonical_cv": "on",
            "configure_match_engine": "on",
        },
        files={"cv_file": ("cv.md", b"ABAP consultant CV", "text/markdown")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Configured+from+CV" in response.headers["location"]
    assert "ABAP consultant" in (project_root / "profile" / "canonical-cv.md").read_text(encoding="utf-8")
    preferences = (project_root / "profile" / "preferences.yaml").read_text(encoding="utf-8")
    assert "remote_policy: required" in preferences
    assert "ABAP variants" in preferences


def test_cv_upload_preview_does_not_apply_profile_sections(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    class FakeLlmService:
        def __init__(self, root):
            pass

        def is_configured(self):
            return True

        def complete(self, prompt, **kwargs):
            assert "Requested sections: canonical_cv, skills" in prompt
            return type(
                "Completion",
                (),
                {
                    "text": (
                        '{"canonical_cv":"# CV\\nPreview only","skills_yaml":{"skills":{"strongest":["ABAP","RAP"]}}}'
                    )
                },
            )()

    monkeypatch.setattr("job_agent.services.setup_service.LlmService", FakeLlmService)

    response = client.post(
        "/setup/cv-reference",
        data={
            "auto_configure_profile": "on",
            "preview_profile_configuration": "on",
            "configure_canonical_cv": "on",
            "configure_skills": "on",
        },
        files={"cv_file": ("cv.md", b"ABAP consultant CV", "text/markdown")},
    )

    assert response.status_code == 200
    assert "Review CV Profile Draft" in response.text
    assert "Apply selected draft sections" in response.text
    assert "Discard draft" in response.text
    assert "Review drafted values" in response.text
    assert "nav-profile-button has-alert" in response.text
    assert "Draft ready" in response.text
    assert 'data-cv-mode-panel="upload" hidden' in response.text
    assert "Upload changed CV" in response.text
    assert "Preview only" not in (project_root / "profile" / "canonical-cv.md").read_text(encoding="utf-8")

    refreshed = client.get("/setup")
    assert refreshed.status_code == 200
    assert "Review CV Profile Draft" in refreshed.text
    assert "summary-action-link" in refreshed.text
    assert "nav-profile-button has-alert" in refreshed.text

    work_items = client.get("/api/work-status").json()["sources"]
    assert any(
        item.get("kind") == "profile"
        and item.get("status") == "completed"
        and item.get("href") == "/setup#cv-profile-draft"
        for item in work_items
    )

    draft_id = re.search(r'name="draft_id" value="([^"]+)"', response.text).group(1)
    discard = client.post("/setup/cv-reference/discard-draft", data={"draft_id": draft_id}, follow_redirects=False)
    assert discard.status_code == 303
    assert "Discarded+CV+profile+draft" in discard.headers["location"]
    assert "Review CV Profile Draft" not in client.get("/setup").text
    assert not any(
        item.get("kind") == "profile" and item.get("href") == "/setup#cv-profile-draft"
        for item in client.get("/api/work-status").json()["sources"]
    )


def test_cv_profile_draft_apply_clears_unreviewed_state(client: TestClient, project_root: Path) -> None:
    draft = CvProfileDraftService(project_root).save_draft(
        {
            "targets": ["canonical_cv"],
            "sections": [
                {
                    "key": "canonical_cv",
                    "label": "CV narrative",
                    "status": "Ready",
                    "summary": "Applied preview",
                }
            ],
            "data": {"canonical_cv": "# Applied preview"},
        },
        source_label="test CV",
        task_id="profile-test",
    )

    response = client.post(
        "/setup/cv-reference/apply-draft",
        data={"draft_id": draft["id"], "configure_canonical_cv": "on"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Applied+CV+draft" in response.headers["location"]
    assert "# Applied preview" in (project_root / "profile" / "canonical-cv.md").read_text(encoding="utf-8")
    setup_page = client.get("/setup").text
    assert "Review CV Profile Draft" not in setup_page
    assert "nav-profile-button has-alert" not in setup_page
    assert "Enhanced from CV" in setup_page


def test_persisted_profile_draft_task_survives_work_status_refresh(client: TestClient, project_root: Path) -> None:
    CvProfileDraftService(project_root).save_task(
        {
            "task_id": "profile-refresh-test",
            "title": "Drafting profile from CV",
            "status": "running",
            "stage": "Calling Claude",
            "message": "Asking Claude to draft structured profile settings from the CV.",
            "progress_percent": 38,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": "",
            "error_message": "",
        }
    )

    payload = client.get("/api/work-status").json()

    assert any(
        item.get("task_id") == "profile-refresh-test"
        and item.get("kind") == "profile"
        and item.get("stage") == "Calling Claude"
        for item in payload["sources"]
    )


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
    assert "Source Results" in response.text
    assert "Interesting Signals" in response.text
    assert "Local" in response.text
    assert "Checking source 1/1: Local" in response.text
    assert "Highlighted match" in response.text
    assert f"run_id={run.run_id}" in response.text
    assert "category_include=weak" in response.text
    assert "posting_status_include=no_longer_posted" in response.text
    assert "View jobs from this day" in response.text


def test_batch_generate_route_redirects_with_counts(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    class FakeResult:
        succeeded = 2
        failed = 1

    class FakeMaterialService:
        def generate_many(self, job_ids, use_llm, llm_model=""):
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

    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert "/match-sandbox?job_id=" in detail.text

    sandbox_href = re.search(r'href="(/match-sandbox\?[^"]+)"', detail.text).group(1)
    sandbox = client.get(sandbox_href)
    assert sandbox.status_code == 200
    assert "SAP ABAP Consultant" in sandbox.text
    assert "Score Result" in sandbox.text


def test_match_sandbox_scores_current_form_values(client: TestClient) -> None:
    response = client.post(
        "/api/match-sandbox/score",
        data={
            "remote_policy": "required",
            "permanent_policy": "penalize",
            "permanent_penalty": "-25",
            "technical_cap": "70",
            "module_cap": "25",
            "technical_rule_label": ["ABAP variants"],
            "technical_rule_terms": ["abap\nsap abap"],
            "technical_rule_score": ["40"],
            "technical_rule_mode": ["required"],
            "module_rule_label": [""],
            "module_rule_terms": [""],
            "module_rule_score": [""],
            "module_rule_mode": ["bonus"],
            "contract_rule_label": ["Contract"],
            "contract_rule_terms": ["contract"],
            "contract_rule_score": ["8"],
            "contract_rule_mode": ["bonus"],
            "title": "SAP OData Consultant",
            "location": "",
            "remote": "Remote",
            "rate": "",
            "contract_duration": "",
            "workload": "",
            "required_skills": "",
            "required_modules": "",
            "description": "OData Gateway contract role.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["category"] == "excluded"
    assert "ABAP variants" in payload["exclusion_reason"]


def test_compatibility_route_renders_report(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    report = {
        "url": "https://example.com/jobs",
    }
    monkeypatch.setattr(
        "job_agent.web.routers.compatibility.check_job_board_compatibility",
        lambda url, render, **kwargs: type(
            "Report",
            (),
            {
                "url": url,
                "input_type": "public URL",
                "normal_html": type(
                    "Quality",
                    (),
                    {
                        "label": "Generic baseline (initial HTML)",
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
                "findings": [],
                "recipe_preview": None,
                "as_dict": lambda self: report,
            },
        )(),
    )

    response = client.post("/compatibility", data={"url": "https://example.com/jobs"}, follow_redirects=False)

    assert response.status_code == 200
    assert "manual intake recommended" in response.text
    assert "Generic Extractor Check" in response.text
    assert "Generic baseline (initial HTML)" in response.text


def test_recipe_preview_route_is_removed(client: TestClient) -> None:
    assert client.get("/recipe-preview").status_code == 404
    assert client.post("/recipe-preview", data={}).status_code == 404


def test_source_test_route_replaces_configured_recipe_review(client: TestClient) -> None:
    response = client.get("/sources/eursap-jobs/test-run")

    assert response.status_code == 200
    assert "Test Source Safely" in response.text
    assert "Run source test" in response.text
    assert '<li class="active">Resolve configured source and selected recipe</li>' not in response.text
    assert "<li>Resolve configured source and selected recipe</li>" in response.text
    assert "execution-running" not in response.text
    assert "Collect jobs from listing records/cards and pagination" in response.text
    assert "What The Reading Plan Did" in response.text


def test_source_detail_shows_latest_source_test_evidence_without_preview_route(
    client: TestClient,
    project_root: Path,
) -> None:
    from job_agent.services.execution_source_service import ExecutionSourceService
    from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
    from job_agent.services.source_registry_service import SourceRegistryService
    from job_agent.services.source_test_service import SourceTestJobPreview, SourceTestResult

    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=False)
    SourceExecutionReadinessService(project_root).save_from_source_test(
        SourceTestResult(
            source_id="eursap-jobs",
            source_name="Eursap Jobs",
            source_type="recipe_html",
            source_enabled=False,
            forced_disabled=True,
            status="success",
            job_count=2,
            pagination_fetch_count=1,
            pagination_unique_jobs_from_fetched_pages=1,
            detail_fetch_count=2,
            detail_verified_listing_page_count=2,
            capability_checks=[
                {
                    "capability": "listing_cards",
                    "status": "pass",
                    "expected": True,
                    "observed": True,
                    "detail": "2 jobs extracted from configured listing cards.",
                }
            ],
            jobs=[
                SourceTestJobPreview(
                    title="SAP Basis Consultant",
                    url="https://eursap.eu/jobs/sap-basis",
                    source="Eursap Jobs",
                    source_id="eursap-jobs",
                )
            ],
        )
    )

    response = client.get("/sources/eursap-jobs")

    assert response.status_code == 200
    assert "Source-test evidence" in response.text
    assert "View source test" in response.text
    assert "/recipe-preview" not in response.text


def test_frontend_debug_state_records_browser_snapshots(client: TestClient, project_root: Path) -> None:
    response = client.post(
        "/api/debug/frontend-state",
        json={
            "feature": "compatibility_browser",
            "action": "page_loaded",
            "page_url": "http://testserver/compatibility",
            "source_forms": [
                {
                    "source_mode": "configured",
                    "selected_source_id": "whitehall-sap-contract",
                    "source_url_value": "https://www.whitehallresources.com/sap-jobs/",
                    "source_url_readonly": True,
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = client.get("/api/debug/frontend-state").json()

    assert payload["latest_by_feature"]["compatibility_browser"]["action"] == "page_loaded"
    assert (
        payload["latest_by_feature"]["compatibility_browser"]["state"]["browser"]["source_forms"][0][
            "selected_source_id"
        ]
        == "whitehall-sap-contract"
    )
    assert (project_root / "output" / "debug" / "frontend-state.json").exists()


def test_source_overview_and_detail_routes_render(client: TestClient) -> None:
    overview = client.get("/sources")

    assert overview.status_code == 200
    assert 'class="source-list"' in overview.text
    assert 'class="source-card' in overview.text
    assert "/sources/new" in overview.text
    assert "/sources/suggest" in overview.text
    assert "Daily-run configured" in overview.text
    assert "Saved sources" in overview.text
    assert "Will run now" in overview.text
    assert "Setup status" in overview.text
    assert "Indexing" in overview.text
    assert "Detail review" in overview.text
    assert "data-source-overview-dynamic" in overview.text
    assert "/api/sources/overview" in overview.text
    assert "Manual Intake" in overview.text
    assert "Eursap Jobs" in overview.text
    assert "Whitehall Resources SAP Jobs" in overview.text
    overview_payload = client.get("/api/sources/overview")

    assert overview_payload.status_code == 200
    overview_fragments = overview_payload.json()
    assert 'class="source-list"' in overview_fragments["overview_html"]
    assert "Eursap Jobs" in overview_fragments["overview_html"]
    assert "prepare_all_html" in overview_fragments

    detail = client.get("/sources/eursap-jobs")

    assert detail.status_code == 200
    assert "Ready for a safe source test" in detail.text
    assert "Source-test evidence" in detail.text
    assert "Review found contents" not in detail.text
    assert "Safe source test" in detail.text
    assert "Run eligibility" in detail.text
    assert "View all jobs from this source" in detail.text
    assert "Listing index" in detail.text
    assert "Initial ingestion" in detail.text
    setup_section = detail.text.split('<div class="panel" id="reading-plan"', 1)[0]
    safe_section = detail.text.split('<div class="panel" id="safe-test"', 1)[1]
    assert "Listing index" in setup_section
    assert "Initial ingestion" in setup_section
    assert "Listing index" not in safe_section
    assert "Ingest all indexed jobs" not in safe_section
    assert "Source settings" in detail.text
    assert "Save source settings" in detail.text
    assert "Capture sample only" in detail.text
    assert "Reading plan" in detail.text
    assert "Use selected plan" in detail.text
    assert "Compatibility evidence" in detail.text
    assert "Recipe editor" in detail.text
    assert "Source-test evidence and fields" in detail.text
    assert "Advanced: regenerate or inspect plans" in detail.text
    assert "Generated plans" in detail.text
    assert "Historical Results" in detail.text
    assert "No jobs have been saved from this source yet" in detail.text
    assert "/recipe-preview" not in detail.text
    assert "auto_run=1" not in detail.text
    assert "selected_source_id=eursap-jobs" in detail.text


def test_source_session_route_records_storage_state(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    project_root: Path,
) -> None:
    from job_agent.io.yaml_store import read_yaml
    from job_agent.services.source_test_service import SourceTestResult

    state_path = project_root / "sources" / "sessions" / "eursap-jobs.storage-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    page = client.get("/sources/eursap-jobs/session")
    assert page.status_code == 200
    assert "Connect Source Session" in page.text
    assert "Not connected" in page.text
    assert "Open sign-in browser" in page.text

    response = client.post(
        "/sources/eursap-jobs/session/connect",
        data={"storage_state_path": "sources/sessions/eursap-jobs.storage-state.json"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/sources/eursap-jobs/session")
    data = read_yaml(project_root / "sources" / "source-sessions.yaml", {})
    assert data["sources"]["eursap-jobs"]["storage_state_path"] == "sources/sessions/eursap-jobs.storage-state.json"
    assert data["sources"]["eursap-jobs"]["verified_at"] == ""

    def fake_run_test(self, source_id, *, force_disabled=False, progress_callback=None):
        return SourceTestResult(
            source_id=source_id,
            source_name="Eursap Jobs",
            source_type="recipe_html",
            source_enabled=False,
            forced_disabled=force_disabled,
            status="success",
            job_count=1,
            source_access_requires_session=True,
            source_access_session_status="connected",
            capability_checks=[
                {
                    "capability": "source_access",
                    "status": "pass",
                    "expected": True,
                    "observed": True,
                    "detail": "Connected source session was used for this verification run.",
                }
            ],
        )

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService.run_test", fake_run_test)
    verify_response = client.post("/sources/eursap-jobs/session/verify", follow_redirects=False)

    assert verify_response.status_code == 303
    verified_data = read_yaml(project_root / "sources" / "source-sessions.yaml", {})
    assert verified_data["sources"]["eursap-jobs"]["verified_at"]


def test_source_session_capture_route_starts_guided_browser_capture(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    class FakeTask:
        task_id = "session-test"

    launched = {}

    def fake_capture(**kwargs):
        launched.update(kwargs)
        return FakeTask()

    monkeypatch.setattr("job_agent.web.routers.sources.runtime.launch_source_session_capture", fake_capture)

    response = client.post(
        "/sources/eursap-jobs/session/capture",
        data={
            "storage_state_path": "sources/sessions/eursap-jobs.storage-state.json",
            "expires_at": "2026-07-01T09:00:00+00:00",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/sources/eursap-jobs/session")
    assert launched["source_id"] == "eursap-jobs"
    assert launched["source_name"] == "Eursap Jobs"
    assert launched["source_url"]
    assert launched["storage_state_path"] == "sources/sessions/eursap-jobs.storage-state.json"
    assert launched["expires_at"] == "2026-07-01T09:00:00+00:00"


def test_add_source_workflow_creates_review_source(client: TestClient, project_root: Path) -> None:
    from job_agent.services.source_registry_service import SourceRegistryService

    form = client.get("/sources/new")
    response = client.post(
        "/sources/new",
        data={
            "name": "Accuro Projects",
            "url": "https://www.accuro.dk/freelance-projects",
            "recipe_path": "",
            "notes": "Potential Nordic freelance source.",
        },
        follow_redirects=False,
    )

    assert form.status_code == 200
    assert "Save source" in form.text
    assert response.status_code == 303
    assert response.headers["location"].startswith("/sources/accuro-projects")
    source = SourceRegistryService(project_root).get_source("accuro-projects")
    assert source is not None
    assert source.kind == "job_board"
    assert source.status == "needs_review"
    assert source.enabled is False
    assert source.url == "https://www.accuro.dk/freelance-projects"


def test_source_suggestions_copy_paste_flow_renders_save_forms(client: TestClient) -> None:
    page = client.get("/sources/suggest")

    assert page.status_code == 200
    assert "Suggest Sources" in page.text
    assert "Claude not connected" in page.text
    assert "Refresh prompt preview" in page.text
    assert "Use another AI" in page.text
    assert "Copy prompt" in page.text
    assert "data-work-status-form" in page.text
    assert "Disqualified domains" in page.text
    assert "SAP ABAP" in page.text
    assert "Prefer broad source URLs" in page.text

    response = client.post(
        "/sources/suggest/parse",
        data={
            "focus": "Nordic contracts",
            "llm_response": (
                '{"sources":[{"name":"Nordic SAP Contracts",'
                '"homepage_url":"https://example.com",'
                '"recommended_listing_url":"https://example.com/jobs?keyword=SAP",'
                '"why_relevant":"Good SAP contract signal",'
                '"expected_signal":"ABAP and RAP roles",'
                '"visit_instructions":"Open jobs, search SAP ABAP, then choose Contract.",'
                '"suggested_filters":["Contract","SAP ABAP"],'
                '"search_terms":["SAP RAP freelance"],'
                '"caveats":"Check whether filters remain in the URL.",'
                '"priority":1}]}'
            ),
        },
    )

    assert response.status_code == 200
    assert "Parsed 1 source suggestions" in response.text
    assert "Nordic SAP Contracts" in response.text
    assert "Open jobs, search SAP ABAP, then choose Contract." in response.text
    assert 'action="/sources/suggest/save"' in response.text
    assert 'action="/sources/suggest/disqualify"' in response.text
    assert 'value="https://example.com/jobs?keyword=SAP"' in response.text

    saved = client.post(
        "/sources/suggest/save",
        data={
            "name": "Nordic SAP Contracts",
            "url": "https://example.com/jobs?keyword=SAP",
            "notes": "Pending setup",
        },
    )
    assert saved.status_code == 200
    saved_payload = saved.json()
    assert saved_payload["ok"] is True
    assert saved_payload["status"] == "added"
    assert saved_payload["source_url"].startswith("/sources/")

    duplicate = client.post(
        "/sources/suggest/save",
        data={
            "name": "Nordic SAP Contracts Again",
            "url": "https://example.com/jobs?keyword=SAP",
            "notes": "Duplicate",
        },
    )
    assert duplicate.status_code == 200
    duplicate_payload = duplicate.json()
    assert duplicate_payload["status"] == "already_added"

    disqualified = client.post(
        "/sources/suggest/save",
        data={
            "name": "Indeed",
            "url": "https://www.indeed.com/jobs?q=sap",
            "notes": "Filtered",
        },
    )
    assert disqualified.status_code == 400
    assert "disqualified" in disqualified.json()["error"]

    duplicate_page = client.post(
        "/sources/suggest/parse",
        data={
            "focus": "Nordic contracts",
            "llm_response": (
                '{"sources":[{"name":"Nordic SAP Contracts",'
                '"homepage_url":"https://example.com",'
                '"recommended_listing_url":"https://example.com/jobs?keyword=SAP",'
                '"why_relevant":"Good SAP contract signal","priority":1}]}'
            ),
        },
    )
    assert "Already added" in duplicate_page.text


def test_source_suggestions_generate_and_external_agent_flows(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    class FakeLlmService:
        def __init__(self, root):
            self.root = root

        def is_configured(self):
            return True

        def complete(self, prompt, **kwargs):
            assert "EU contracts" in prompt
            assert kwargs["purpose"] == "source_suggestion"
            return type(
                "Completion",
                (),
                {
                    "text": (
                        '{"sources":[{"name":"Example SAP Jobs",'
                        '"homepage_url":"https://example.com",'
                        '"recommended_listing_url":"https://example.com/jobs",'
                        '"why_relevant":"Relevant SAP roles","priority":2}]}'
                    ),
                    "model": "fake-sonnet",
                },
            )()

    monkeypatch.setattr("job_agent.services.source_suggestion_service.LlmService", FakeLlmService)

    response = client.post("/sources/suggest/generate", data={"focus": "EU contracts"})

    assert response.status_code == 200
    assert "Generated 1 source suggestions" in response.text
    assert "Example SAP Jobs" in response.text
    assert "fake-sonnet" in response.text

    prepared = client.post("/sources/suggest/external-agent/prepare", data={"focus": "Remote SAP"})
    assert prepared.status_code == 200
    payload = prepared.json()
    assert payload["ok"] is True
    assert payload["purpose"] == "source_suggestion"
    assert "Remote SAP" in payload["prompt"]

    applied = client.post(
        "/sources/suggest/external-agent/apply",
        data={
            "interaction_id": payload["interaction_id"],
            "response_text": (
                '{"sources":[{"name":"External SAP Jobs",'
                '"homepage_url":"https://example.com",'
                '"recommended_listing_url":"https://example.com/sap"}]}'
            ),
        },
    )
    assert applied.status_code == 200
    assert applied.json()["ok"] is True
    redirected = client.get(applied.json()["redirect_url"])
    assert redirected.status_code == 200
    assert "External SAP Jobs" in redirected.text


def test_source_detail_updates_selected_recipe(client: TestClient, project_root: Path) -> None:
    from job_agent.services.source_registry_service import SourceRegistryService

    response = client.post(
        "/sources/eursap-jobs/recipe/update",
        data={"recipe_path": "sources/recipes/experimental/whitehall-sap-contract.yaml"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    assert source.recipe_path == "sources/recipes/experimental/whitehall-sap-contract.yaml"
    assert source.kind == "recipe"


def test_source_detail_updates_registry_fields(client: TestClient, project_root: Path) -> None:
    response = client.post(
        "/sources/eursap-jobs/registry/update",
        data={
            "name": "Eursap Jobs Reviewed",
            "kind": "recipe",
            "url": "https://eursap.eu/jobs?contract=sap",
            "status": "ready",
            "recipe_path": "sources/recipes/experimental/eursap-jobs.yaml",
            "notes": "Ready for controlled source test.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/sources/eursap-jobs")
    detail = client.get("/sources/eursap-jobs")
    assert "Eursap Jobs Reviewed" in detail.text
    assert "https://eursap.eu/jobs?contract=sap" in detail.text
    assert "Ready for controlled source test." in detail.text


def test_source_archive_hides_source_from_default_overview_and_disables_execution(
    client: TestClient, project_root: Path
) -> None:
    from job_agent.io.yaml_store import read_yaml

    client.post("/sources/whitehall-sap-contract/execution/create", follow_redirects=False)

    archive_response = client.post("/sources/whitehall-sap-contract/archive", follow_redirects=False)

    assert archive_response.status_code == 303
    assert archive_response.headers["location"].startswith("/sources")
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    assert config["sources"][0]["enabled"] is False

    overview = client.get("/sources")

    active_section = overview.text.split("Archived sources", 1)[0]
    assert "Whitehall Resources SAP Jobs" not in active_section
    assert "Archived sources (1)" in overview.text
    assert "Whitehall Resources SAP Jobs" in overview.text
    assert "Restore" in overview.text

    restore_response = client.post("/sources/whitehall-sap-contract/restore", follow_redirects=False)

    assert restore_response.status_code == 303
    detail = client.get("/sources/whitehall-sap-contract")
    assert "Needs setup" in detail.text


def test_source_detail_capture_calibration_action_is_bounded(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    captured = {}

    class FakeCalibrationResult:
        url = "https://eursap.eu/jobs"
        artifact_dir = project_root / "output" / "recipe-calibration" / "fake-artifact"
        capture_mode = "static_html"
        candidate_count = 12
        recipe_extracted_count = 3
        detail_sample_url = ""
        warnings = []

    def fake_capture(url, recipe_path, rendered, root, max_candidates, capture_detail, **kwargs):
        captured.update(
            {
                "url": url,
                "recipe_path": recipe_path,
                "rendered": rendered,
                "root": root,
                "max_candidates": max_candidates,
                "capture_detail": capture_detail,
            }
        )
        return FakeCalibrationResult()

    monkeypatch.setattr("job_agent.web.workflows.capture_recipe_calibration", fake_capture)

    response = client.post(
        "/sources/eursap-jobs/recipe-calibration/capture",
        data={"max_candidates": "500", "capture_detail": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Calibration+artifact+captured" in response.headers["location"]
    assert captured == {
        "url": "https://eursap.eu/jobs",
        "recipe_path": "sources/recipes/experimental/eursap-jobs.yaml",
        "rendered": None,
        "root": project_root,
        "max_candidates": 50,
        "capture_detail": True,
    }


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
    compatibility = client.get(
        "/compatibility?source_mode=configured&selected_source_id=eursap-jobs"
        "&recipe_path=sources/recipes/experimental/eursap-jobs.yaml&show_saved=1"
    )
    source_test = client.get("/sources/eursap-jobs/test-run")

    assert overview.status_code == 200
    assert detail.status_code == 200
    assert "Source-test evidence and fields" in detail.text
    assert "No source test readiness has been saved yet." in detail.text
    assert "auto_run=1" not in detail.text
    assert compatibility.status_code == 200
    assert "Saved Source / Recipe Result" in compatibility.text
    assert "9 jobs extracted, 9 useful titles, no generic labels." in compatibility.text
    assert source_test.status_code == 200
    assert "Test Source Safely" in source_test.text


def test_source_detail_safe_test_panel_uses_session_cta_when_access_blocked(
    client: TestClient, project_root: Path
) -> None:
    from job_agent.services.recipe_preview_service import RecipePreviewResult
    from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
    from job_agent.services.source_health_service import SourceHealthService
    from job_agent.services.source_session_service import SourceSessionService
    from job_agent.services.source_test_service import SourceTestResult

    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)
    SourceHealthService(project_root).save_preview(
        "eursap-jobs",
        RecipePreviewResult(
            recipe_source_name="Eursap Jobs",
            recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
            recipe_status="experimental",
            input_type="local artifact",
            input_value="output/recipe-calibration/eursap/page.html",
            base_url="https://eursap.eu/jobs",
            mode_used="local_fixture_html",
            extracted_job_count=22,
            useful_titles=22,
            generic_labels=0,
            unique_urls=22,
            average_description_length=120,
            jobs=[],
            warnings=[],
        ),
    )
    state_path = project_root / "sources" / "sessions" / "eursap-jobs.storage-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    SourceSessionService(project_root).record_storage_state(
        "eursap-jobs",
        session_scope="eursap.eu",
        storage_state_path=state_path.relative_to(project_root).as_posix(),
    )
    SourceExecutionReadinessService(project_root).save_from_source_test(
        SourceTestResult(
            source_id="eursap-jobs",
            source_name="Eursap Jobs",
            source_type="recipe_html",
            source_enabled=False,
            forced_disabled=True,
            status="warning",
            job_count=22,
            capability_checks=[
                {
                    "capability": "pagination_navigation",
                    "status": "fail",
                    "detail": "Fetched 2 pagination page(s), but later pages may require a logged-in session.",
                },
                {
                    "capability": "source_access",
                    "status": "fail",
                    "detail": "The page still showed a sign-in gate.",
                },
            ],
        )
    )

    detail = client.get("/sources/eursap-jobs")
    safe_test_start = detail.text.index('<div class="panel" id="safe-test">')
    safe_test_end = detail.text.index('<div class="panel">\n  <h2>Historical Results</h2>', safe_test_start)
    safe_test_panel = detail.text[safe_test_start:safe_test_end]

    assert 'href="/sources/eursap-jobs/test-run?start=1">Test source safely</a>' in safe_test_panel
    assert 'href="/sources/eursap-jobs/session">Verify session</a>' not in safe_test_panel


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
    assert "Promising results" in overview.text
    assert "1 high, 0 interesting" in overview.text
    assert detail.status_code == 200
    assert "Result history" in detail.text
    assert "SAP ABAP Consultant" in detail.text
    assert "Last seen run" in detail.text


def test_viewing_source_pages_does_not_mutate_execution_config(client: TestClient, project_root: Path) -> None:
    source_config = project_root / "sources" / "recruiting-sites.yaml"
    source_config.write_text(
        "sources:\n  - name: Local Sample\n    type: local_yaml\n    path: jobs/raw/sample_jobs.yaml\n",
        encoding="utf-8",
    )
    before = source_config.read_text(encoding="utf-8")

    assert client.get("/sources").status_code == 200
    assert client.get("/sources/eursap-jobs").status_code == 200

    assert source_config.read_text(encoding="utf-8") == before


def test_source_execution_routes_create_guard_enable_and_disable(client: TestClient, project_root: Path) -> None:
    from job_agent.io.yaml_store import read_yaml
    from job_agent.models import Job
    from job_agent.services.recipe_preview_service import RecipePreviewResult
    from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
    from job_agent.services.source_health_service import SourceHealthService
    from job_agent.services.source_listing_index_store import SourceListingIndexStore
    from job_agent.services.source_test_service import SourceTestJobPreview, SourceTestResult

    create_response = client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)
    recipe_file = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    recipe_file.parent.mkdir(parents=True, exist_ok=True)
    recipe_file.write_text(
        "source_name: Eursap Jobs\n"
        "mode: static_html\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a\n"
        "  link_selector: a\n",
        encoding="utf-8",
    )
    os.utime(recipe_file, (1, 1))

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
    SourceExecutionReadinessService(project_root).save_from_source_test(
        SourceTestResult(
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
    )
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

    enable_response = client.post("/sources/eursap-jobs/execution/enable", follow_redirects=False)

    assert enable_response.status_code == 303
    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    assert config["sources"][0]["enabled"] is True
    future_timestamp = datetime.now(UTC).timestamp() + 5
    os.utime(recipe_file, (future_timestamp, future_timestamp))
    detail = client.get("/sources/eursap-jobs")
    assert "Running in daily checks" not in detail.text
    assert '<span class="badge high">Implemented</span>' not in detail.text
    assert "Reading plan changed since the saved source test" in detail.text
    assert "Safe Source Test" in detail.text
    assert "Needs retest" in detail.text
    assert "This source is included in automatic job checks." not in detail.text

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
    assert "Ready for a safe source test" in detail.text
    assert "Safe source test" in detail.text


def test_source_test_run_view_and_api_save_readiness(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from job_agent.services.source_test_service import SourceTestJobPreview, SourceTestResult

    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)

    class FakeSourceTestService:
        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            assert source_id == "eursap-jobs"
            assert force_disabled is True
            if progress_callback:
                progress_callback(
                    {
                        "phase": "Listing page request",
                        "status": "running",
                        "detail": "Fetching listing page.",
                        "capability": "listing",
                    }
                )
            return SourceTestResult(
                source_id="eursap-jobs",
                source_name="Eursap Jobs",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="success",
                job_count=13,
                recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
                recipe_source_name="Eursap Jobs (experimental)",
                mode_used="static_html",
                run_steps=[
                    {
                        "phase": "Pagination detection",
                        "status": "completed",
                        "detail": "Proof fetched 1 pagination page.",
                        "capability": "pagination",
                    }
                ],
                pagination_configured=True,
                pagination_fetch_count=1,
                pagination_fetch_attempts=["https://eursap.eu/jobs/page/2"],
                listing_observed_count=14,
                listing_extracted_count=13,
                listing_duplicate_count=1,
                listing_pages=[
                    {
                        "page_url": "https://eursap.eu/jobs",
                        "observed_cards": 14,
                        "extracted_jobs": 13,
                        "missing_url_count": 0,
                        "rejected_count": 0,
                        "duplicate_count": 1,
                        "limit_skipped_count": 0,
                        "limit": 25,
                    }
                ],
                seen_new_count=10,
                seen_changed_count=1,
                seen_previously_seen_count=2,
                count_explanations=[
                    "Observed 14 listing card(s) and retained 13 job(s): 1 duplicate URL(s) were ignored.",
                    "Seen-state check: 10 new, 1 changed, 2 already seen in previous runs.",
                ],
                detail_follow_enabled=True,
                detail_fetch_count=13,
                detail_enriched_count=13,
                detail_attempts=[
                    {
                        "url": "https://eursap.eu/jobs/sap-basis",
                        "status": "completed",
                        "found_fields": ["description"],
                        "missing_fields": [],
                        "detail": "Found detail fields: description.",
                    }
                ],
                jobs=[
                    SourceTestJobPreview(
                        title=f"SAP Basis Consultant {index}",
                        url=f"https://eursap.eu/jobs/sap-basis-{index}",
                        source="Eursap Jobs",
                        source_id="eursap-jobs",
                        location="Remote",
                        description="Detailed SAP Basis role with enough text to verify full extracted descriptions.",
                        description_preview="Detailed SAP Basis role.",
                    )
                    for index in range(13)
                ],
            )

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    view = client.get("/sources/eursap-jobs/test-run")
    response = client.post("/sources/eursap-jobs/test-run")
    stream_response = client.post("/sources/eursap-jobs/test-run/stream")

    assert view.status_code == 200
    assert "Test Source Safely" in view.text
    assert "<summary>Live run log</summary>" in view.text
    assert "source-test-insight" in view.text
    assert "work-status-card" in view.text
    assert "source-test-results" in view.text
    assert "?result=" in view.text
    assert "shouldStartImmediately" in view.text
    assert "consumeImmediateStartFlag" in view.text
    assert "/sources/eursap-jobs/test-run/stream" in view.text
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["job_count"] == 13
    assert len(payload["jobs"]) == 13
    assert payload["jobs"][0]["title"] == "SAP Basis Consultant 0"
    assert payload["jobs"][0]["description"].startswith("Detailed SAP Basis role")
    assert payload["pagination_fetch_attempts"] == ["https://eursap.eu/jobs/page/2"]
    assert payload["detail_fetch_count"] == 13
    assert payload["listing_observed_count"] == 14
    assert payload["listing_duplicate_count"] == 1
    assert payload["seen_previously_seen_count"] == 2
    assert "already seen" in payload["count_explanations"][1]
    assert payload["readiness_status"] in {"ready", "blocked", "warning"}
    assert payload["source_test_insight"]["title"] == "Source test passed"
    assert stream_response.status_code == 200
    assert '"type": "progress"' in stream_response.text
    assert '"type": "complete"' in stream_response.text


def test_source_test_insight_can_rebuild_reading_plan_with_clues(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    from job_agent.services.source_test_service import SourceTestResult

    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)

    class FakeSourceTestService:
        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            return SourceTestResult(
                source_id=source_id,
                source_name="Eursap Jobs",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="warning",
                job_count=22,
                warning_count=2,
                warnings=["Page 2 returned only listings already seen."],
                pagination_strategy="url",
                pagination_fetch_count=2,
                pagination_duplicate_ratio=1.0,
                pagination_unique_jobs_from_fetched_pages=0,
                visible_total_job_count=75,
                capability_checks=[
                    {
                        "capability": "listing_total_access",
                        "status": "fail",
                        "expected": True,
                        "observed": False,
                        "detail": "The listing page appears to advertise 75 posting(s), but the verified extractor reached only 22.",
                    },
                    {
                        "capability": "pagination_strategy",
                        "status": "fail",
                        "expected": True,
                        "observed": True,
                        "detail": "Recipe declares url pagination, but proof-fetched pages returned only duplicate listings.",
                    },
                ],
            )

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    payload = client.post("/sources/eursap-jobs/test-run").json()

    assert payload["source_test_insight"]["title"] == "Paginated page access failed"
    assert payload["source_test_insight"]["action"]["action"] == "/sources/eursap-jobs/reading-plan/rebuild-from-test"
    assert payload["source_test_insight"]["generation_clues"]["pagination_strategy_tested"] == "url"

    captured: dict[str, object] = {}

    class RunService:
        def start_from_source_capture(self, source_id: str, **kwargs):
            captured["source_id"] = source_id
            captured.update(kwargs)
            return {"run_id": "run-from-test"}

    monkeypatch.setattr("job_agent.web.workflows.RecipeGenerationRunService", lambda root: RunService())

    response = client.post("/sources/eursap-jobs/reading-plan/rebuild-from-test", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/sources/eursap-jobs/recipe-generation/run-from-test"
    assert captured["source_test_insight"]["insight_title"] == "Paginated page access failed"
    assert captured["source_test_insight"]["pagination_strategy_tested"] == "url"


def test_source_test_insight_prioritizes_failed_source_access(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    project_root: Path,
) -> None:
    from job_agent.services.recipe_preview_service import RecipePreviewResult
    from job_agent.services.source_health_service import SourceHealthService
    from job_agent.services.source_test_service import SourceTestResult

    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)
    SourceHealthService(project_root).save_preview(
        "eursap-jobs",
        RecipePreviewResult(
            recipe_source_name="Eursap Jobs",
            recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
            recipe_status="experimental",
            input_type="local artifact",
            input_value="output/recipe-calibration/eursap/page.html",
            base_url="https://eursap.eu/jobs",
            mode_used="local_fixture_html",
            extracted_job_count=22,
            useful_titles=22,
            generic_labels=0,
            unique_urls=22,
            average_description_length=120,
            jobs=[],
            warnings=[],
        ),
    )

    class FakeSourceTestService:
        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False, progress_callback=None):
            return SourceTestResult(
                source_id=source_id,
                source_name="Eursap Jobs",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="warning",
                job_count=22,
                pagination_strategy="browser_click",
                pagination_fetch_count=1,
                pagination_duplicate_ratio=1.0,
                source_access_requires_session=True,
                source_access_session_status="connected",
                source_access_session_label="Connected",
                source_access_login_gate_detected=True,
                capability_checks=[
                    {
                        "capability": "pagination_strategy",
                        "status": "fail",
                        "expected": True,
                        "observed": True,
                        "detail": "Browser-click pagination was blocked by a login modal.",
                    },
                    {
                        "capability": "source_access",
                        "status": "fail",
                        "expected": True,
                        "observed": False,
                        "detail": "A connected source session was used, but the page still showed a sign-in or registration gate.",
                    },
                ],
            )

    monkeypatch.setattr("job_agent.web.source_workflow.SourceTestService", FakeSourceTestService)

    payload = client.post("/sources/eursap-jobs/test-run").json()

    assert payload["source_test_insight"]["title"] == "Source access needs attention"
    assert payload["source_test_insight"]["action"]["href"] == "/sources/eursap-jobs/session"
    assert payload["source_test_insight"]["generation_clues"]["source_access_login_gate_detected"] is True
    assert payload["readiness_summary"].startswith("Blocked: Source access verification failed")
    assert payload["readiness_blockers"][0].startswith("Source access verification failed")


def test_rebuild_from_test_redirects_to_retest_when_plan_changed(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    project_root: Path,
) -> None:
    from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
    from job_agent.services.source_test_service import SourceTestResult

    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)
    SourceExecutionReadinessService(project_root).save_from_source_test(
        SourceTestResult(
            source_id="eursap-jobs",
            source_name="Eursap Jobs",
            source_type="recipe_html",
            source_enabled=False,
            forced_disabled=True,
            status="warning",
            job_count=22,
            capability_checks=[
                {
                    "capability": "pagination_strategy",
                    "status": "fail",
                    "detail": "URL pagination returned only duplicate listings.",
                }
            ],
        )
    )
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text("source_name: Eursap Jobs\nlisting: {}\n", encoding="utf-8")
    future_timestamp = datetime.now(UTC).timestamp() + 60
    os.utime(recipe_path, (future_timestamp, future_timestamp))

    class RunService:
        def start_from_source_capture(self, source_id: str, **kwargs):
            raise AssertionError("stale source-test insight should not trigger generation")

    monkeypatch.setattr("job_agent.web.workflows.RecipeGenerationRunService", lambda root: RunService())

    response = client.post("/sources/eursap-jobs/reading-plan/rebuild-from-test", follow_redirects=False)
    detail = client.get("/sources/eursap-jobs")

    assert response.status_code == 303
    assert response.headers["location"] == "/sources/eursap-jobs/test-run?start=1"
    assert "Previous source-test details are from an older reading plan." in detail.text
    assert "Reading plan changed since the saved source test" in detail.text


def test_source_detail_run_now_requires_enabled_source(client: TestClient) -> None:
    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)

    response = client.post("/sources/eursap-jobs/run-now", follow_redirects=False)

    assert response.status_code == 303
    assert "warning=" in response.headers["location"]


def test_source_detail_run_now_redirects_to_run_detail(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    from job_agent.services.execution_source_service import ExecutionSourceService
    from job_agent.services.single_source_run_service import SingleSourceRunResult

    client.post("/sources/eursap-jobs/execution/create", follow_redirects=False)
    ExecutionSourceService(project_root).enable("eursap-jobs")

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

    monkeypatch.setattr("job_agent.web.source_workflow.SingleSourceRunService", FakeSingleSourceRunService)

    response = client.post("/sources/eursap-jobs/run-now", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/runs/run-1"


def test_source_detail_index_and_investigate_actions_redirect(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, project_root: Path
) -> None:
    from job_agent.models import Job
    from job_agent.services.source_listing_index_store import SourceListingIndexStore

    class FakeRecord:
        run_id = "run-1"

    class FakeIndexTask:
        source_name = "Eursap Jobs"

    launched_index = {}
    launched_detail = {}

    def fake_index_launch(source_id, source_name=""):
        launched_index.update({"source_id": source_id, "source_name": source_name})
        return FakeIndexTask()

    def fake_launch(source_id, *, include_disabled_source=False, append_to_today=True):
        launched_detail.update(
            {
                "source_id": source_id,
                "include_disabled_source": include_disabled_source,
                "append_to_today": append_to_today,
            }
        )
        return FakeRecord()

    class ReadyReadiness:
        readiness_status = "ready"
        blockers = []

    monkeypatch.setattr(
        "job_agent.web.source_workflow.SourceExecutionReadinessService",
        lambda root: type("Svc", (), {"evaluate": lambda self, source_id: ReadyReadiness()})(),
    )
    monkeypatch.setattr("job_agent.web.routers.sources.runtime.launch_source_listing_index", fake_index_launch)
    monkeypatch.setattr("job_agent.web.routers.sources.runtime.launch_source_detail_run", fake_launch)

    index_response = client.post("/sources/eursap-jobs/index-listings", follow_redirects=False)
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
    investigate_response = client.post("/sources/eursap-jobs/investigate-all", follow_redirects=False)

    assert index_response.status_code == 303
    assert "Listing+index+refresh+started+for+Eursap+Jobs" in index_response.headers["location"]
    assert launched_index == {"source_id": "eursap-jobs", "source_name": "Eursap Jobs"}
    assert investigate_response.status_code == 303
    assert investigate_response.headers["location"] == "/runs/run-1"
    assert launched_detail == {
        "source_id": "eursap-jobs",
        "include_disabled_source": True,
        "append_to_today": True,
    }


def test_source_detail_index_and_investigate_actions_require_ready_source(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    launched = {"index": False, "detail": False}

    class BlockedReadiness:
        readiness_status = "blocked"
        blockers = ["No saved source test readiness result."]

    monkeypatch.setattr(
        "job_agent.web.source_workflow.SourceExecutionReadinessService",
        lambda root: type("Svc", (), {"evaluate": lambda self, source_id: BlockedReadiness()})(),
    )
    monkeypatch.setattr(
        "job_agent.web.routers.sources.runtime.launch_source_listing_index",
        lambda *args, **kwargs: launched.update(index=True),
    )
    monkeypatch.setattr(
        "job_agent.web.routers.sources.runtime.launch_source_detail_run",
        lambda *args, **kwargs: launched.update(detail=True),
    )

    index_response = client.post("/sources/eursap-jobs/index-listings", follow_redirects=False)
    investigate_response = client.post("/sources/eursap-jobs/investigate-all", follow_redirects=False)

    assert index_response.status_code == 303
    assert "No+saved+source+test+readiness+result" in index_response.headers["location"]
    assert investigate_response.status_code == 303
    assert "No+saved+source+test+readiness+result" in investigate_response.headers["location"]
    assert launched == {"index": False, "detail": False}


def test_manual_source_cannot_create_recipe_execution_route(client: TestClient) -> None:
    response = client.post("/sources/manual-intake/execution/create", follow_redirects=False)

    assert response.status_code == 400


def test_jobs_row_click_handler_allows_rows_inside_bulk_form(client: TestClient) -> None:
    response = client.get("/jobs")

    assert response.status_code == 200
    assert 'closest("a, button, input, select, textarea, label")' in response.text
    assert 'closest("a, button, input, select, textarea, label, form")' not in response.text
