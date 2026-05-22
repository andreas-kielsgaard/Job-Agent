from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import TemplateNotFound

from job_agent.io.json_store import read_json
from job_agent.run_service import run_daily_agent
from job_agent.run_store import RunOptions, RunStore
from job_agent.services.ai_search_service import AiSearchEvaluation


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


def test_run_daily_agent_scores_each_source_before_next_source_starts(template_project: Path) -> None:
    _write_two_source_project(template_project)

    result = run_daily_agent(RunOptions(include_seen=True, generate_materials=False), root=template_project)

    events = RunStore(template_project).read_events(result.record.run_id)
    event_types = [event["event_type"] for event in events]
    first_score_index = event_types.index("job_scored")
    second_source_started_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "source_started" and event["current_source"] == "Second Source"
    )
    source_processed = [event for event in events if event["event_type"] == "source_processed"]
    highlights = [event for event in events if event["event_type"] == "match_highlight"]

    assert first_score_index < second_source_started_index
    assert len(source_processed) == 2
    assert source_processed[0]["counts"]["jobs_found"] == 1
    assert source_processed[0]["counts"]["candidates_processed"] == 1
    assert source_processed[0]["counts"]["highlighted_matches"] == 1
    assert highlights
    assert highlights[0]["counts"]["score"] == 70
    assert "strong match category" in highlights[0]["message"]
    assert highlights[0]["current_source"] == "First Source"
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
