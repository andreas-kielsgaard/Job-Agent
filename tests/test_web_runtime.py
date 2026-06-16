from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from job_agent.io.yaml_store import read_yaml
from job_agent.models import Job
from job_agent.run_store import RunEvent, RunOptions, RunStore
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_listing_index_store import SourceListingIndexStore
from job_agent.services.source_registry_service import SourceRegistryService
from job_agent.services.source_test_service import SourceTestResult
from job_agent.web.runtime import SourceSessionCaptureTask, WebRuntime, utc_now


def test_source_session_capture_task_saves_session_and_reports_active_work(
    monkeypatch,
    project_root: Path,
) -> None:
    runtime = WebRuntime(project_root)
    task = SourceSessionCaptureTask(
        task_id="session-test",
        source_id="sample-source",
        source_name="Sample Source",
        source_url="https://example.com/jobs",
        session_scope="example.com",
        storage_state_path="sources/sessions/sample-source.storage-state.json",
        started_at=utc_now(),
        href="/sources/sample-source/session",
    )
    with runtime._session_lock:
        runtime._session_tasks[task.task_id] = task

    def fake_capture(capture_task):
        state_path = project_root / capture_task.storage_state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    monkeypatch.setattr(runtime, "_capture_source_session_to_file", fake_capture)

    runtime._run_source_session_capture(task.task_id)

    data = read_yaml(project_root / "sources" / "source-sessions.yaml", {})
    record = data["sources"]["sample-source"]
    assert record["storage_state_path"] == "sources/sessions/sample-source.storage-state.json"
    assert record["session_scope"] == "example.com"
    assert record["verified_at"] == ""

    active = runtime.active_work_payload()["sources"][0]
    assert active["kind"] == "session"
    assert active["status"] == "completed"
    assert active["is_running"] is False
    assert active["progress_percent"] == 100
    assert active["href"] == "/sources/sample-source/session"


def test_active_daily_run_uses_single_aggregate_work_widget(project_root: Path) -> None:
    runtime = WebRuntime(project_root)
    store = RunStore(project_root)
    record = store.create_run(RunOptions())
    store.update(record.run_id, status="running")
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
            "Completed source 1/2: First - 3 jobs found, 0 warnings",
            phase="source_ingestion",
            current_source="First",
            counts={"source_index": 1, "source_count": 2, "jobs_found": 3},
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

    items = runtime.active_work_payload()["sources"]

    assert len(items) == 1
    assert items[0]["kind"] == "run"
    assert items[0]["is_running"] is True
    assert items[0]["task_id"] == f"run-{record.run_id}"
    assert items[0]["title"] == "Daily run"
    assert items[0]["stage"] == "1/2 sources finished"
    assert "1 running in parallel" in items[0]["message"]
    assert items[0]["href"] == f"/runs/{record.run_id}"


def test_full_source_ingestion_launcher_records_uncapped_options(project_root: Path) -> None:
    runtime = WebRuntime(project_root)
    submitted = []
    runtime.executor.submit = lambda *args: submitted.append(args)

    record = runtime.launch_full_source_ingestion()

    assert record.options["full_source_ingestion"] is True
    assert record.options["include_disabled_sources"] is True
    assert record.options["detail_extraction_limit"] is None
    assert record.options["detail_pause_every_jobs"] == 25
    assert record.options["detail_pause_seconds"] == 20.0
    assert submitted


def test_source_session_capture_reports_launching_before_capture_window_is_ready(
    monkeypatch,
    project_root: Path,
) -> None:
    runtime = WebRuntime(project_root)
    task = SourceSessionCaptureTask(
        task_id="session-test",
        source_id="sample-source",
        source_name="Sample Source",
        source_url="https://example.com/jobs",
        session_scope="example.com",
        storage_state_path="sources/sessions/sample-source.storage-state.json",
        started_at=utc_now(),
        href="/sources/sample-source/session",
    )
    with runtime._session_lock:
        runtime._session_tasks[task.task_id] = task
    observed = {}

    def fake_capture(capture_task):
        active = runtime.active_work_payload()["sources"][0]
        observed.update(active)
        state_path = project_root / capture_task.storage_state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    monkeypatch.setattr(runtime, "_capture_source_session_to_file", fake_capture)

    runtime._run_source_session_capture(task.task_id)

    assert observed["kind"] == "session"
    assert observed["status"] == "running"
    assert observed["is_running"] is True
    assert observed["stage"] == "Launching browser"
    assert "Starting a sign-in browser" in observed["message"]


def test_new_source_session_capture_supersedes_active_capture(project_root: Path) -> None:
    runtime = WebRuntime(project_root)

    class FakeExecutor:
        def __init__(self) -> None:
            self.submitted: list[str] = []

        def submit(self, fn, task_id):
            self.submitted.append(task_id)

    fake_executor = FakeExecutor()
    runtime.session_executor = fake_executor
    first = runtime.launch_source_session_capture(
        source_id="sample-source",
        source_name="Sample Source",
        source_url="https://example.com/jobs",
        session_scope="example.com",
        storage_state_path="sources/sessions/sample-source.storage-state.json",
    )

    second = runtime.launch_source_session_capture(
        source_id="sample-source",
        source_name="Sample Source",
        source_url="https://example.com/jobs",
        session_scope="example.com",
        storage_state_path="sources/sessions/sample-source.storage-state.json",
    )

    with runtime._session_lock:
        first_task = runtime._session_tasks[first.task_id]
        second_task = runtime._session_tasks[second.task_id]
    assert first_task.status == "failed"
    assert first_task.stage == "Superseded"
    assert first_task.finished_at
    assert second_task.status == "pending"
    assert fake_executor.submitted == [first.task_id, second.task_id]


def test_superseded_source_session_capture_does_not_save_session(
    monkeypatch,
    project_root: Path,
) -> None:
    runtime = WebRuntime(project_root)
    task = SourceSessionCaptureTask(
        task_id="session-stale",
        source_id="sample-source",
        source_name="Sample Source",
        source_url="https://example.com/jobs",
        session_scope="example.com",
        storage_state_path="sources/sessions/sample-source.storage-state.json",
        status="failed",
        stage="Superseded",
        started_at=utc_now(),
        finished_at=utc_now(),
    )
    with runtime._session_lock:
        runtime._session_tasks[task.task_id] = task

    def fail_if_called(capture_task):
        raise AssertionError("superseded capture should not run")

    monkeypatch.setattr(runtime, "_capture_source_session_to_file", fail_if_called)

    runtime._run_source_session_capture(task.task_id)

    assert read_yaml(project_root / "sources" / "source-sessions.yaml", {}) == {}


def test_bulk_source_auto_setup_queues_setup_ready_sources_only(project_root: Path) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    registry = SourceRegistryService(project_root)
    first = registry.add_source(name="First Jobs", url="https://first.example/jobs")
    second = registry.add_source(name="Second Jobs", url="https://second.example/careers")
    registry.add_source(name="Homepage", url="https://homepage.example")
    registry.add_source(name="LinkedIn", url="https://www.linkedin.com/jobs/search")
    runtime = WebRuntime(project_root)

    class FakeExecutor:
        def __init__(self) -> None:
            self.submitted: list[str] = []

        def submit(self, fn, task_id):
            self.submitted.append(task_id)

    fake_executor = FakeExecutor()
    runtime.auto_setup_executor = fake_executor

    tasks = runtime.launch_all_source_auto_setups()

    assert {task.source_id for task in tasks} == {first.id, second.id}
    assert len(fake_executor.submitted) == 2
    assert runtime.auto_setup_max_workers == 10


def test_bulk_source_auto_setup_dedupes_duplicate_overview_cards(monkeypatch, project_root: Path) -> None:
    (project_root / ".env").write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    source = SourceRegistryService(project_root).add_source(name="First Jobs", url="https://first.example/jobs")
    runtime = WebRuntime(project_root)

    class FakeExecutor:
        def __init__(self) -> None:
            self.submitted: list[str] = []

        def submit(self, fn, task_id):
            self.submitted.append(task_id)

    fake_executor = FakeExecutor()
    runtime.auto_setup_executor = fake_executor
    duplicate_card = {"source": SimpleNamespace(id=source.id), "auto_setup": {"can_start": True}}
    monkeypatch.setattr(
        "job_agent.web.source_workflow.SourceWorkflowHandler.overview_context",
        lambda self: {"source_cards": [duplicate_card, duplicate_card]},
    )

    tasks = runtime.launch_all_source_auto_setups()

    assert [task.source_id for task in tasks] == [source.id]
    assert len(fake_executor.submitted) == 1


def test_bulk_source_auto_setup_queues_stale_recipe_refresh_without_llm_key(project_root: Path) -> None:
    registry = SourceRegistryService(project_root)
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "stale-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(
        "source_name: Stale Jobs\n"
        "start_url: https://stale.example/jobs\n"
        "mode: static_html\n"
        "listing:\n"
        "  card_selector: a.job-card\n"
        "  title_selector: a.job-card\n"
        "  link_selector: a.job-card\n",
        encoding="utf-8",
    )
    source = registry.add_source(
        name="Stale Jobs",
        url="https://stale.example/jobs",
        recipe_path="sources/recipes/experimental/stale-jobs.yaml",
    )
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=True)
    SourceExecutionReadinessService(project_root).save_from_source_test(
        SourceTestResult(
            source_id=source.id,
            source_name=source.name,
            source_type="recipe_html",
            source_enabled=True,
            status="success",
            job_count=1,
        )
    )
    SourceListingIndexStore(project_root).record_index(
        source_id=source.id,
        source_name=source.name,
        jobs=[Job(title="SAP Basis Consultant", source=source.name, source_id=source.id, url="https://stale.example/1")],
    )
    future_timestamp = recipe_path.stat().st_mtime + 60
    os.utime(recipe_path, (future_timestamp, future_timestamp))
    runtime = WebRuntime(project_root)

    class FakeExecutor:
        def __init__(self) -> None:
            self.submitted: list[str] = []

        def submit(self, fn, task_id):
            self.submitted.append(task_id)

    fake_executor = FakeExecutor()
    runtime.auto_setup_executor = fake_executor

    tasks = runtime.launch_all_source_auto_setups()

    assert {task.source_id for task in tasks} == {source.id}
    assert len(fake_executor.submitted) == 1
