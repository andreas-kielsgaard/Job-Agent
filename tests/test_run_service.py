from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import TemplateNotFound

from job_agent.io.json_store import read_json
from job_agent.run_service import run_daily_agent
from job_agent.run_store import RunOptions, RunStore


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
