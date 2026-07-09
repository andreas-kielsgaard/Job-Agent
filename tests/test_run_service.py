from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import TemplateNotFound

from job_agent.io.json_store import read_json
from job_agent.models import Job
from job_agent.run_service import run_daily_agent
from job_agent.run_store import RunOptions, RunStore
from job_agent.services.ai_search_service import AiSearchEvaluation
from job_agent.services.source_listing_index_store import SourceListingIndexStore
from job_agent.services.source_run_field_health_service import SourceRunFieldHealthService
from job_agent.services.source_session_service import SourceSessionService


def test_run_daily_agent_completes_and_writes_outputs(local_yaml_source_project: Path) -> None:
    result = run_daily_agent(
        RunOptions(include_seen=True, mark_seen=False, generate_materials=True),
        root=local_yaml_source_project,
    )

    record = result.record
    assert record.status == "completed"
    assert Path(record.run_log_path).exists()
    assert Path(record.events_path).exists()
    assert Path(record.digest_path).exists()
    assert Path(record.excluded_path).exists()
    assert result.digest_items
    assert list((local_yaml_source_project / "output").glob("*/*/index.json"))
    assert read_json(local_yaml_source_project / "jobs" / "application_status.json", [])
    assert read_json(local_yaml_source_project / "jobs" / "seen_jobs.json", []) == []


def test_run_options_ai_enhanced_search_defaults_false() -> None:
    assert RunOptions().ai_enhanced_search is False
    assert RunOptions().generate_materials is False
    assert RunOptions().full_source_ingestion is False
    assert RunOptions().include_disabled_sources is False
    assert RunOptions().wait_for_source_access is False


def test_default_run_writes_placeholder_package_without_calling_generator(
    monkeypatch: pytest.MonkeyPatch, local_yaml_source_project: Path
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("generate_materials should not be called by default")

    monkeypatch.setattr("job_agent.run_service.generate_materials", fail_if_called)

    result = run_daily_agent(RunOptions(include_seen=True), root=local_yaml_source_project)

    assert result.record.status == "completed"
    index = read_json(next((local_yaml_source_project / "output").glob("*/*/index.json")), {})
    assert index["materials_generated"] is False
    assert index["material_status"] == "missing"


def test_explicit_material_generation_writes_generated_status(local_yaml_source_project: Path) -> None:
    run_daily_agent(RunOptions(include_seen=True, generate_materials=True), root=local_yaml_source_project)

    index = read_json(next((local_yaml_source_project / "output").glob("*/*/index.json")), {})
    assert index["materials_generated"] is True
    assert index["material_status"] == "generated"


def test_run_without_ai_enhanced_search_does_not_call_ai_service(
    monkeypatch: pytest.MonkeyPatch, local_yaml_source_project: Path
) -> None:
    class FailingAiSearchService:
        def __init__(self, root: Path) -> None:
            raise AssertionError("AI search should not be constructed")

    monkeypatch.setattr("job_agent.run_service.AiSearchService", FailingAiSearchService)

    run_daily_agent(
        RunOptions(include_seen=True, generate_materials=False, ai_enhanced_search=False),
        root=local_yaml_source_project,
    )


def test_run_with_ai_enhanced_search_stores_package_fields_and_events(
    monkeypatch: pytest.MonkeyPatch, local_yaml_source_project: Path
) -> None:
    class FakeAiSearchService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def is_configured(self) -> bool:
            return True

        def evaluate(self, *args, **kwargs) -> AiSearchEvaluation:
            return AiSearchEvaluation(
                status="evaluated",
                summary="Strong ABAP fit with Gateway evidence.",
                recommended_angle="Lead with ABAP/Gateway delivery.",
                fit_confidence="high",
                match_score=90,
                employment_conditions={"employment_type": "contract", "remote": "hybrid"},
                risk_flags=["Confirm rate"],
                key_profile_evidence=["ABAP", "OData"],
                should_prioritize=True,
                model="fake-model",
            )

    monkeypatch.setattr("job_agent.run_service.AiSearchService", FakeAiSearchService)

    result = run_daily_agent(
        RunOptions(include_seen=True, generate_materials=False, ai_enhanced_search=True),
        root=local_yaml_source_project,
    )

    events = RunStore(local_yaml_source_project).read_events(result.record.run_id)
    assert any(event["event_type"] == "ai_evaluation_started" for event in events)
    assert any(event["event_type"] == "ai_evaluation_completed" for event in events)
    source_processed = next(event for event in events if event["event_type"] == "source_processed")
    assert source_processed["counts"]["ai_evaluations_completed"] == 1
    assert source_processed["counts"]["ai_prioritized"] == 1

    index = read_json(next((local_yaml_source_project / "output").glob("*/*/index.json")), {})
    assert index["ai_evaluation_status"] == "evaluated"
    assert index["ai_summary"] == "Strong ABAP fit with Gateway evidence."
    assert index["ai_match_score"] == 90
    assert index["match_score"] == round((index["deterministic_match_score"] + 90) / 2)
    assert index["ai_employment_conditions"]["remote"] == "hybrid"
    assert index["ai_should_prioritize"] is True


def test_ai_evaluation_failure_does_not_fail_run(
    monkeypatch: pytest.MonkeyPatch, local_yaml_source_project: Path
) -> None:
    class FailingAiSearchService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def is_configured(self) -> bool:
            return True

        def failed(self, error: str) -> AiSearchEvaluation:
            return AiSearchEvaluation(status="failed", error=error, model="fake-model")

        def evaluate(self, *args, **kwargs) -> AiSearchEvaluation:
            raise RuntimeError("Claude unavailable")

    monkeypatch.setattr("job_agent.run_service.AiSearchService", FailingAiSearchService)

    result = run_daily_agent(
        RunOptions(include_seen=True, generate_materials=False, ai_enhanced_search=True),
        root=local_yaml_source_project,
    )

    assert result.record.status == "completed"
    events = RunStore(local_yaml_source_project).read_events(result.record.run_id)
    assert any(event["event_type"] == "ai_evaluation_failed" for event in events)
    index = read_json(next((local_yaml_source_project / "output").glob("*/*/index.json")), {})
    assert index["ai_evaluation_status"] == "failed"
    assert "Claude unavailable" in index["ai_error"]


def test_ai_enhanced_search_missing_key_skips_without_failing(local_yaml_source_project: Path) -> None:
    result = run_daily_agent(
        RunOptions(include_seen=True, generate_materials=False, ai_enhanced_search=True),
        root=local_yaml_source_project,
    )

    assert result.record.status == "completed"
    events = RunStore(local_yaml_source_project).read_events(result.record.run_id)
    assert any(event["event_type"] == "ai_evaluation_skipped" for event in events)
    index = read_json(next((local_yaml_source_project / "output").glob("*/*/index.json")), {})
    assert index["ai_evaluation_status"] == "skipped"


def test_run_daily_agent_records_source_progress_events(local_yaml_source_project: Path) -> None:
    result = run_daily_agent(
        RunOptions(include_seen=True, mark_seen=False, generate_materials=False),
        root=local_yaml_source_project,
    )

    events = RunStore(local_yaml_source_project).read_events(result.record.run_id)
    source_started = next(event for event in events if event["event_type"] == "source_started")
    source_completed = next(event for event in events if event["event_type"] == "source_completed")

    assert source_started["phase"] == "source_ingestion"
    assert source_started["current_source"] == "Local Sample"
    assert source_started["counts"]["source_index"] == 1
    assert source_started["counts"]["source_count"] == 1
    assert source_completed["counts"]["jobs_found"] == 1


def test_run_daily_agent_can_fetch_sources_before_scoring_completes(template_project: Path) -> None:
    _write_two_source_project(template_project)

    result = run_daily_agent(RunOptions(include_seen=True, generate_materials=False), root=template_project)

    events = RunStore(template_project).read_events(result.record.run_id)
    event_types = [event["event_type"] for event in events]
    first_score_index = event_types.index("job_scored")
    source_started_indexes = [index for index, event in enumerate(events) if event["event_type"] == "source_started"]
    source_processed = [event for event in events if event["event_type"] == "source_processed"]
    highlights = [event for event in events if event["event_type"] == "match_highlight"]

    assert len(source_started_indexes) == 2
    assert max(source_started_indexes) < first_score_index
    assert len(source_processed) == 2
    assert {event["current_source"] for event in source_processed} == {"First Source", "Second Source"}
    assert all(event["counts"]["jobs_found"] == 1 for event in source_processed)
    assert sum(event["counts"]["candidates_processed"] for event in source_processed) == 1
    assert sum(event["counts"]["duplicates_skipped"] for event in source_processed) == 1
    assert sum(event["counts"]["highlighted_matches"] for event in source_processed) == 1
    assert highlights
    assert all(highlight["counts"]["score"] > 0 for highlight in highlights)
    assert all("strong core keyword overlap" in highlight["message"] for highlight in highlights)
    assert result.record.total_loaded == 2


def test_run_daily_agent_skips_duplicate_jobs_across_sources(template_project: Path) -> None:
    _write_two_source_project(template_project, duplicate=True)

    result = run_daily_agent(RunOptions(include_seen=True, generate_materials=False), root=template_project)

    events = RunStore(template_project).read_events(result.record.run_id)
    duplicate_events = [event for event in events if event["event_type"] == "job_duplicate_skipped"]
    source_processed = [event for event in events if event["event_type"] == "source_processed"]

    assert result.record.total_loaded == 2
    assert result.record.new_roles == 1
    assert len(result.digest_items) == 1
    assert len(list((template_project / "output").glob("*/*/index.json"))) == 1
    assert duplicate_events
    assert source_processed[-1]["counts"]["duplicates_skipped"] == 1


def test_mark_seen_and_test_run_seen_rules(local_yaml_source_project: Path) -> None:
    run_daily_agent(
        RunOptions(include_seen=True, mark_seen=True, generate_materials=False), root=local_yaml_source_project
    )
    assert read_json(local_yaml_source_project / "jobs" / "seen_jobs.json", [])

    second_root = local_yaml_source_project
    (second_root / "jobs" / "seen_jobs.json").unlink()
    run_daily_agent(
        RunOptions(include_seen=True, mark_seen=True, generate_materials=False, is_test=True),
        root=second_root,
    )
    assert read_json(second_root / "jobs" / "seen_jobs.json", []) == []


def test_run_daily_agent_explains_previously_seen_skips(local_yaml_source_project: Path) -> None:
    run_daily_agent(
        RunOptions(include_seen=True, mark_seen=True, generate_materials=False), root=local_yaml_source_project
    )

    result = run_daily_agent(
        RunOptions(include_seen=False, mark_seen=False, generate_materials=False), root=local_yaml_source_project
    )

    events = RunStore(local_yaml_source_project).read_events(result.record.run_id)
    source_processed = next(event for event in events if event["event_type"] == "source_processed")
    assert source_processed["counts"]["jobs_found"] == 1
    assert source_processed["counts"]["previously_seen"] == 1
    assert source_processed["counts"]["previously_seen_skipped"] == 1
    assert "already seen skipped" in source_processed["message"]


def test_daily_run_reviews_only_unseen_details_and_leaves_over_limit_jobs_unseen(
    monkeypatch: pytest.MonkeyPatch, template_project: Path
) -> None:
    _write_recipe_source_project(template_project, job_count=3)
    detail_calls = []

    def fake_fetch_static(url: str, timeout_seconds: int):
        return _listing_html(3), url, []

    def fake_detail_get(url: str, *args, **kwargs):
        detail_calls.append(url)
        return _FakeResponse(
            f"<main><div class='detail'>Strong ABAP RAP CDS OData Gateway S/4HANA contract role from {url}.</div></main>",
            url=url,
        )

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)
    monkeypatch.setattr("requests.get", fake_detail_get)

    first = run_daily_agent(
        RunOptions(mark_seen=True, generate_materials=False, detail_extraction_limit=2),
        root=template_project,
    )

    assert [url.rsplit("/", 1)[-1] for url in detail_calls] == ["job-1", "job-2"]
    assert len(read_json(template_project / "jobs" / "seen_jobs.json", [])) == 2
    first_events = RunStore(template_project).read_events(first.record.run_id)
    first_source = next(event for event in first_events if event["event_type"] == "source_processed")
    assert first_source["counts"]["jobs_found"] == 3
    assert first_source["counts"]["reviewed_in_detail_count"] == 2
    assert first_source["counts"]["detail_limit_skipped_count"] == 1

    detail_calls.clear()
    second = run_daily_agent(
        RunOptions(mark_seen=True, generate_materials=False, detail_extraction_limit=2),
        root=template_project,
    )

    assert [url.rsplit("/", 1)[-1] for url in detail_calls] == ["job-3"]
    assert len(read_json(template_project / "jobs" / "seen_jobs.json", [])) == 3
    second_events = RunStore(template_project).read_events(second.record.run_id)
    second_source = next(event for event in second_events if event["event_type"] == "source_processed")
    assert second_source["counts"]["previously_seen_skipped"] == 2
    assert second_source["counts"]["reviewed_in_detail_count"] == 1
    assert second_source["counts"]["detail_limit_skipped_count"] == 0


def test_daily_run_detail_review_limit_is_per_source(monkeypatch: pytest.MonkeyPatch, template_project: Path) -> None:
    _write_multi_recipe_source_project(template_project, source_count=2, job_count=3)
    detail_calls = []

    def fake_fetch_static(url: str, timeout_seconds: int):
        return _listing_html(3), url, []

    def fake_detail_get(url: str, *args, **kwargs):
        detail_calls.append(url)
        return _FakeResponse(
            f"<main><div class='detail'>Strong ABAP RAP CDS OData Gateway S/4HANA contract role from {url}.</div></main>",
            url=url,
        )

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)
    monkeypatch.setattr("requests.get", fake_detail_get)

    result = run_daily_agent(
        RunOptions(mark_seen=True, generate_materials=False, detail_extraction_limit=2),
        root=template_project,
    )

    assert len(detail_calls) == 4
    assert sum("source-1.example.com" in url for url in detail_calls) == 2
    assert sum("source-2.example.com" in url for url in detail_calls) == 2

    events = RunStore(template_project).read_events(result.record.run_id)
    processed = {
        event["current_source"]: event["counts"] for event in events if event["event_type"] == "source_processed"
    }
    assert set(processed) == {"Detail Source 1", "Detail Source 2"}
    assert all(counts["reviewed_in_detail_count"] == 2 for counts in processed.values())
    assert all(counts["detail_limit_skipped_count"] == 1 for counts in processed.values())

    limit_events = [event for event in events if event["event_type"] == "detail_limit_reached"]
    assert {event["current_source"] for event in limit_events} == {"Detail Source 1", "Detail Source 2"}


def test_full_source_ingestion_reviews_all_detail_pages_with_batch_pause(
    monkeypatch: pytest.MonkeyPatch, template_project: Path
) -> None:
    _write_recipe_source_project(template_project, job_count=3)
    detail_calls = []
    pauses = []

    def fake_fetch_static(url: str, timeout_seconds: int):
        return _listing_html(3), url, []

    def fake_detail_get(url: str, *args, **kwargs):
        detail_calls.append(url)
        return _FakeResponse(
            f"<main><div class='detail'>Strong ABAP RAP CDS OData Gateway S/4HANA contract role from {url}.</div></main>",
            url=url,
        )

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)
    monkeypatch.setattr("requests.get", fake_detail_get)
    monkeypatch.setattr("job_agent.run_service.time.sleep", lambda seconds: pauses.append(seconds))

    result = run_daily_agent(
        RunOptions(
            mark_seen=True,
            generate_materials=False,
            detail_extraction_limit=None,
            full_source_ingestion=True,
            include_disabled_sources=True,
            detail_pause_every_jobs=2,
            detail_pause_seconds=3.0,
        ),
        root=template_project,
    )

    assert [url.rsplit("/", 1)[-1] for url in detail_calls] == ["job-1", "job-2", "job-3"]
    assert pauses == [3.0]
    events = RunStore(template_project).read_events(result.record.run_id)
    source = next(event for event in events if event["event_type"] == "source_processed")
    assert source["counts"]["reviewed_in_detail_count"] == 3
    assert source["counts"]["detail_limit_skipped_count"] == 0
    assert any(event["event_type"] == "detail_review_pause" for event in events)


def test_daily_run_records_latest_source_field_health_warning(
    monkeypatch: pytest.MonkeyPatch, template_project: Path
) -> None:
    _write_recipe_source_project(template_project, job_count=1)

    def fake_fetch_static(url: str, timeout_seconds: int):
        return _listing_html(1), url, []

    def fake_detail_get(url: str, *args, **kwargs):
        return _FakeResponse("<main><div class='detail'>SAP ABAP Consultant 1</div></main>", url=url)

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)
    monkeypatch.setattr("requests.get", fake_detail_get)

    result = run_daily_agent(RunOptions(mark_seen=True, generate_materials=False), root=template_project)

    record = SourceRunFieldHealthService(template_project).get("detail-source")
    events = RunStore(template_project).read_events(result.record.run_id)
    assert record.status == "needs_relearn"
    assert record.required_missing_fields == ["description"]
    assert any(event["event_type"] == "source_field_health_checked" for event in events)
    assert any("required field coverage failed" in warning.message for warning in result.source_warnings)


def test_daily_run_reuses_connected_session_for_authenticated_detail_review(
    monkeypatch: pytest.MonkeyPatch, template_project: Path
) -> None:
    _write_recipe_source_project(template_project, job_count=1, requires_session=True)
    state_path = template_project / "sources" / "sessions" / "detail-source.storage-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"cookies": [{"name": "sid", "value": "abc", "domain": "example.com", "path": "/"}], "origins": []}',
        encoding="utf-8",
    )
    SourceSessionService(template_project).record_storage_state(
        "detail-source",
        session_scope="example.com",
        storage_state_path=state_path.relative_to(template_project).as_posix(),
    )
    SourceSessionService(template_project).mark_verified("detail-source", session_scope="example.com")
    listing_session_paths = []
    detail_cookie_names = []

    def fake_fetch_static(url: str, timeout_seconds: int, **kwargs):
        listing_session_paths.append(str(kwargs.get("session_state_path") or ""))
        return _listing_html(1), url, []

    def fake_detail_get(url: str, *args, **kwargs):
        detail_cookie_names.extend(cookie.name for cookie in kwargs.get("cookies", []))
        return _FakeResponse(
            f"<main><div class='detail'>Strong ABAP RAP CDS OData Gateway S/4HANA contract role from {url}.</div></main>",
            url=url,
        )

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)
    monkeypatch.setattr("requests.get", fake_detail_get)

    run_daily_agent(RunOptions(mark_seen=True, generate_materials=False), root=template_project)

    assert any("detail-source.storage-state.json" in path for path in listing_session_paths)
    assert "sid" in detail_cookie_names
    assert len(read_json(template_project / "jobs" / "seen_jobs.json", [])) == 1


def test_mass_runs_skip_required_missing_session_before_fetch(
    monkeypatch: pytest.MonkeyPatch, template_project: Path
) -> None:
    _write_recipe_source_project(template_project, job_count=1, requires_session=True)
    fetch_calls = []

    def fake_fetch_static(url: str, timeout_seconds: int, **kwargs):
        fetch_calls.append(url)
        return _listing_html(1), url, []

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)

    daily = run_daily_agent(RunOptions(mark_seen=True, generate_materials=False), root=template_project)
    full = run_daily_agent(
        RunOptions(mark_seen=True, generate_materials=False, full_source_ingestion=True),
        root=template_project,
    )

    daily_events = RunStore(template_project).read_events(daily.record.run_id)
    full_events = RunStore(template_project).read_events(full.record.run_id)
    assert fetch_calls == []
    assert daily.record.total_loaded == 0
    assert full.record.total_loaded == 0
    assert any("requires a connected session" in event["message"] for event in daily_events)
    assert any("requires a connected session" in event["message"] for event in full_events)


def test_daily_run_waits_for_required_session_and_resumes_fetch(
    monkeypatch: pytest.MonkeyPatch, template_project: Path
) -> None:
    _write_recipe_source_project(template_project, job_count=1, requires_session=True)
    fetch_calls = []

    def fake_fetch_static(url: str, timeout_seconds: int, **kwargs):
        fetch_calls.append(str(kwargs.get("session_state_path") or ""))
        return _listing_html(1), url, []

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)

    def on_event(event):
        if event.event_type != "source_access_waiting":
            return
        state_path = template_project / "sources" / "sessions" / "detail-source.storage-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
        service = SourceSessionService(template_project)
        service.record_storage_state(
            "detail-source",
            session_scope="example.com",
            storage_state_path=state_path.relative_to(template_project).as_posix(),
        )
        service.mark_verified("detail-source", session_scope="example.com")

    result = run_daily_agent(
        RunOptions(
            mark_seen=False,
            generate_materials=False,
            detail_extraction_limit=0,
            wait_for_source_access=True,
            source_access_wait_timeout_seconds=1.0,
            source_access_wait_poll_seconds=0.01,
        ),
        progress_callback=on_event,
        root=template_project,
    )

    events = RunStore(template_project).read_events(result.record.run_id)
    assert result.record.total_loaded == 1
    assert any("detail-source.storage-state.json" in path for path in fetch_calls)
    assert any(event["event_type"] == "source_access_waiting" for event in events)
    assert any(event["event_type"] == "source_access_resumed" for event in events)


def test_daily_run_skips_setup_incomplete_source_before_fetch(
    monkeypatch: pytest.MonkeyPatch, template_project: Path
) -> None:
    _write_recipe_source_project(template_project, job_count=1)
    _write_blocked_source_readiness(template_project)
    fetch_calls = []

    def fake_fetch_static(url: str, timeout_seconds: int, **kwargs):
        fetch_calls.append(url)
        return _listing_html(1), url, []

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)

    result = run_daily_agent(RunOptions(mark_seen=True, generate_materials=False, is_test=True), root=template_project)

    events = RunStore(template_project).read_events(result.record.run_id)
    setup_skips = [event for event in events if event["event_type"] == "source_setup_skipped"]
    assert fetch_calls == []
    assert result.record.source_warnings == 0
    assert read_json(template_project / "jobs" / "seen_jobs.json", []) == []
    assert not any(event["event_type"] == "source_started" for event in events)
    assert any("Skipped sources still in setup" in event["message"] for event in setup_skips)


def test_generate_materials_false_writes_placeholder_and_skips_generator(
    monkeypatch: pytest.MonkeyPatch, local_yaml_source_project: Path
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("generate_materials should not be called")

    monkeypatch.setattr("job_agent.run_service.generate_materials", fail_if_called)

    result = run_daily_agent(
        RunOptions(include_seen=True, generate_materials=False),
        root=local_yaml_source_project,
    )

    assert result.record.status == "completed"
    index = read_json(next((local_yaml_source_project / "output").glob("*/*/index.json")), {})
    assert index["materials_generated"] is False


def test_source_warning_does_not_crash_run(template_project: Path) -> None:
    (template_project / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n  - name: Mystery\n    type: unsupported\n",
        encoding="utf-8",
    )

    result = run_daily_agent(RunOptions(include_seen=True), root=template_project)

    assert result.record.status == "completed"
    assert result.record.source_warnings == 1


def test_run_failure_persists_status_message_and_event(local_yaml_source_project: Path) -> None:
    for template in (local_yaml_source_project / "templates").glob("*.j2"):
        template.unlink()

    with pytest.raises(TemplateNotFound):
        run_daily_agent(RunOptions(include_seen=True, generate_materials=True), root=local_yaml_source_project)

    failed = RunStore(local_yaml_source_project).list_runs()[0]
    assert failed.status == "failed"
    assert failed.finished_at
    assert failed.error_message
    events = RunStore(local_yaml_source_project).read_events(failed.run_id)
    assert events[-1]["event_type"] == "run_failed"
    assert "failed" in Path(failed.run_log_path).read_text(encoding="utf-8").lower()


def _write_two_source_project(root: Path, duplicate: bool = False) -> None:
    first_url = "https://example.com/shared" if duplicate else "https://example.com/first"
    second_url = "https://example.com/shared" if duplicate else "https://example.com/second"
    first_source = "Shared Source" if duplicate else "First Source"
    second_source = "Shared Source" if duplicate else "Second Source"
    (root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n"
        "  - name: First Source\n"
        "    type: local_yaml\n"
        "    path: jobs/raw/first.yaml\n"
        "  - name: Second Source\n"
        "    type: local_yaml\n"
        "    path: jobs/raw/second.yaml\n",
        encoding="utf-8",
    )
    (root / "jobs" / "raw" / "first.yaml").write_text(
        "jobs:\n"
        "  - title: SAP ABAP RAP Consultant\n"
        "    company: Recruiter\n"
        f"    source: {first_source}\n"
        f"    url: {first_url}\n"
        "    location: Copenhagen\n"
        "    remote: Hybrid\n"
        "    posted_date: 2026-05-06\n"
        "    description: Strong ABAP RAP CDS OData Gateway S/4HANA contract role.\n",
        encoding="utf-8",
    )
    (root / "jobs" / "raw" / "second.yaml").write_text(
        "jobs:\n"
        "  - title: SAP ABAP RAP Consultant\n"
        "    company: Recruiter\n"
        f"    source: {second_source}\n"
        f"    url: {second_url}\n"
        "    location: Copenhagen\n"
        "    remote: Hybrid\n"
        "    posted_date: 2026-05-06\n"
        "    description: Strong ABAP RAP CDS OData Gateway S/4HANA contract role.\n",
        encoding="utf-8",
    )


def _write_recipe_source_project(root: Path, *, job_count: int, requires_session: bool = False) -> None:
    recipe_path = root / "sources" / "recipes" / "detail-source.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    access_yaml = (
        "access:\n"
        "  requires_session: true\n"
        "  session_scope: example.com\n"
        "  setup_hint: Connect a source session before reading details.\n"
        if requires_session
        else ""
    )
    recipe_path.write_text(
        "source_name: Detail Source\n"
        "mode: static_html\n"
        f"{access_yaml}"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a.job-link\n"
        "  link_selector: a.job-link\n"
        "  description_selector: .summary\n"
        "detail:\n"
        "  follow: true\n"
        "  description_selector: .detail\n"
        "  request_delay_seconds: 0\n"
        "accept:\n"
        "  url_contains:\n"
        "    - /jobs/\n",
        encoding="utf-8",
    )
    (root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n"
        "  - name: Detail Source\n"
        "    source_id: detail-source\n"
        "    type: recipe_html\n"
        "    url: https://example.com/jobs\n"
        f"    recipe_path: {recipe_path.relative_to(root).as_posix()}\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    (root / "sources" / "source-registry.yaml").write_text(
        "sources:\n"
        "  - id: detail-source\n"
        "    name: Detail Source\n"
        "    kind: recipe\n"
        "    status: testing\n"
        "    url: https://example.com/jobs\n"
        f"    recipe_path: {recipe_path.relative_to(root).as_posix()}\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    (root / "sources" / "source-execution-readiness.yaml").write_text(
        "sources:\n"
        "  detail-source:\n"
        "    last_checked_at: '2999-01-01T00:00:00+00:00'\n"
        "    dry_run_status: success\n"
        f"    dry_run_job_count: {job_count}\n"
        "    dry_run_warning_count: 0\n"
        "    dry_run_warnings: []\n"
        "    dry_run_capability_checks: []\n"
        "    dry_run_pagination_duplicate_page_count: 0\n"
        "    dry_run_pagination_duplicate_ratio: 0.0\n"
        f"    dry_run_pagination_unique_jobs_from_fetched_pages: {job_count}\n"
        "    readiness_status: ready\n"
        "    readiness_summary: Ready.\n"
        "    checks: {}\n"
        "    blockers: []\n"
        "    warnings: []\n",
        encoding="utf-8",
    )
    SourceListingIndexStore(root).record_index(
        source_id="detail-source",
        source_name="Detail Source",
        jobs=[
            Job(
                title=f"SAP ABAP Consultant {index}",
                source="Detail Source",
                source_id="detail-source",
                url=f"https://example.com/jobs/job-{index}",
            )
            for index in range(1, job_count + 1)
        ],
    )


def _write_multi_recipe_source_project(root: Path, *, source_count: int, job_count: int) -> None:
    source_lines = ["sources:"]
    registry_lines = ["sources:"]
    readiness_lines = ["sources:"]
    for source_index in range(1, source_count + 1):
        source_id = f"detail-source-{source_index}"
        source_name = f"Detail Source {source_index}"
        source_url = f"https://source-{source_index}.example.com/jobs"
        recipe_path = root / "sources" / "recipes" / f"{source_id}.yaml"
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        recipe_path.write_text(
            f"source_name: {source_name}\n"
            "mode: static_html\n"
            "listing:\n"
            "  card_selector: article.job-card\n"
            "  title_selector: a.job-link\n"
            "  link_selector: a.job-link\n"
            "  description_selector: .summary\n"
            "detail:\n"
            "  follow: true\n"
            "  description_selector: .detail\n"
            "  request_delay_seconds: 0\n"
            "accept:\n"
            "  url_contains:\n"
            "    - /jobs/\n",
            encoding="utf-8",
        )
        relative_recipe_path = recipe_path.relative_to(root).as_posix()
        source_lines.extend(
            [
                f"  - name: {source_name}",
                f"    source_id: {source_id}",
                "    type: recipe_html",
                f"    url: {source_url}",
                f"    recipe_path: {relative_recipe_path}",
                "    enabled: true",
            ]
        )
        registry_lines.extend(
            [
                f"  - id: {source_id}",
                f"    name: {source_name}",
                "    kind: recipe",
                "    status: testing",
                f"    url: {source_url}",
                f"    recipe_path: {relative_recipe_path}",
                "    enabled: true",
            ]
        )
        readiness_lines.extend(
            [
                f"  {source_id}:",
                "    last_checked_at: '2999-01-01T00:00:00+00:00'",
                "    dry_run_status: success",
                f"    dry_run_job_count: {job_count}",
                "    dry_run_warning_count: 0",
                "    dry_run_warnings: []",
                "    dry_run_capability_checks: []",
                "    dry_run_pagination_duplicate_page_count: 0",
                "    dry_run_pagination_duplicate_ratio: 0.0",
                f"    dry_run_pagination_unique_jobs_from_fetched_pages: {job_count}",
                "    readiness_status: ready",
                "    readiness_summary: Ready.",
                "    checks: {}",
                "    blockers: []",
                "    warnings: []",
            ]
        )
        SourceListingIndexStore(root).record_index(
            source_id=source_id,
            source_name=source_name,
            jobs=[
                Job(
                    title=f"SAP ABAP Consultant {source_index}-{job_index}",
                    source=source_name,
                    source_id=source_id,
                    url=f"{source_url}/job-{job_index}",
                )
                for job_index in range(1, job_count + 1)
            ],
        )

    (root / "sources" / "recruiting-sites.yaml").write_text(
        "\n".join(source_lines) + "\n",
        encoding="utf-8",
    )
    (root / "sources" / "source-registry.yaml").write_text(
        "\n".join(registry_lines) + "\n",
        encoding="utf-8",
    )
    (root / "sources" / "source-execution-readiness.yaml").write_text(
        "\n".join(readiness_lines) + "\n",
        encoding="utf-8",
    )


def _write_blocked_source_readiness(root: Path) -> None:
    recipe_path = "sources/recipes/detail-source.yaml"
    (root / "sources" / "source-registry.yaml").write_text(
        "sources:\n"
        "  - id: detail-source\n"
        "    name: Detail Source\n"
        "    kind: recipe\n"
        "    status: testing\n"
        "    url: https://example.com/jobs\n"
        f"    recipe_path: {recipe_path}\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    (root / "sources" / "source-health.yaml").write_text(
        "sources:\n"
        "  detail-source:\n"
        "    last_preview_at: '2026-06-04T00:00:00+00:00'\n"
        "    extracted_job_count: 1\n"
        "    useful_titles: 1\n"
        "    unique_urls: 1\n"
        "    health_status: good\n"
        "    health_summary: 1 job extracted, 1 useful title, no generic labels.\n",
        encoding="utf-8",
    )
    (root / "sources" / "source-execution-readiness.yaml").write_text(
        "sources:\n"
        "  detail-source:\n"
        "    last_checked_at: '2999-01-01T00:00:00+00:00'\n"
        "    dry_run_status: warning\n"
        "    dry_run_job_count: 1\n"
        "    dry_run_warning_count: 1\n"
        "    dry_run_warnings: []\n"
        "    dry_run_capability_checks:\n"
        "      - capability: pagination_navigation\n"
        "        status: fail\n"
        "        detail: Later listing pages require a verified source session.\n"
        "    dry_run_pagination_duplicate_page_count: 1\n"
        "    dry_run_pagination_duplicate_ratio: 1.0\n"
        "    readiness_status: blocked\n"
        "    readiness_summary: Blocked.\n"
        "    checks: {}\n"
        "    blockers:\n"
        "      - Pagination verification failed: Later listing pages require a verified source session.\n"
        "    warnings: []\n",
        encoding="utf-8",
    )


def _listing_html(job_count: int) -> str:
    return "\n".join(
        (
            "<article class='job-card'>"
            f"<a class='job-link' href='/jobs/job-{index}'>SAP ABAP Consultant {index}</a>"
            "<p class='summary'>SAP contract role.</p>"
            "</article>"
        )
        for index in range(1, job_count + 1)
    )


class _FakeResponse:
    def __init__(self, text: str, url: str = "https://example.com/jobs") -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None
