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


def test_app_version_payload_detects_outdated_server_and_schedules_restart(monkeypatch, project_root: Path) -> None:
    tracked_file = project_root / "app" / "code" / "job_agent" / "web" / "templates" / "base.html"
    tracked_file.parent.mkdir(parents=True, exist_ok=True)
    tracked_file.write_text("old template\n", encoding="utf-8")
    runtime = WebRuntime(project_root)
    runtime.startup()
    assert runtime.app_version

    tracked_file.write_text("new template\n", encoding="utf-8")
    payload = runtime.app_version_payload()
    assert payload["outdated"] is True
    assert payload["running_version"] == runtime.app_version
    assert payload["disk_version"] != runtime.app_version

    scheduled: list[float] = []
    monkeypatch.setenv("JOB_AGENT_APP_RESTART_DELAY_SECONDS", "0")
    monkeypatch.setattr("job_agent.web.runtime._schedule_restart", lambda delay: scheduled.append(delay))

    result = runtime.request_restart()
    second = runtime.request_restart()

    assert result["ok"] is True
    assert second["ok"] is True
    assert scheduled == [0.0]


def test_app_restart_waits_for_active_run(monkeypatch, project_root: Path) -> None:
    runtime = WebRuntime(project_root)
    store = RunStore(project_root)
    record = store.create_run(RunOptions())
    store.update(record.run_id, status="running")
    scheduled: list[float] = []
    monkeypatch.setattr("job_agent.web.runtime._schedule_restart", lambda delay: scheduled.append(delay))

    payload = runtime.app_version_payload()
    result = runtime.request_restart()

    assert payload["restart_supported"] is False
    assert record.run_id in payload["restart_blocker"]
    assert result["ok"] is False
    assert record.run_id in result["message"]
    assert scheduled == []


def test_runtime_change_events_wait_and_return_revision(project_root: Path) -> None:
    runtime = WebRuntime(project_root)

    first = runtime.notify_change("work_status", scope="source_index", source_id="dice", task_id="idx-1")
    second = runtime.notify_change("source_overview", scope="source_index", source_id="dice", task_id="idx-1")
    events = runtime.wait_for_changes(int(first["revision"]) - 1, timeout_seconds=0.1)
    later = runtime.wait_for_changes(int(second["revision"]), timeout_seconds=0.1)

    assert [event["kind"] for event in events] == ["work_status", "source_overview"]
    assert events[-1]["revision"] == second["revision"]
    assert later[0]["kind"] == "heartbeat"
    assert later[0]["revision"] == second["revision"]


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


def test_active_daily_run_exposes_source_access_wait_action(project_root: Path) -> None:
    runtime = WebRuntime(project_root)
    store = RunStore(project_root)
    record = store.create_run(RunOptions(wait_for_source_access=True))
    store.update(record.run_id, status="running")
    store.append_event(
        RunEvent(
            record.run_id,
            "source_started",
            "Checking source 1/1: Dice",
            phase="source_ingestion",
            current_source="Dice",
            counts={"source_index": 1, "source_count": 1},
        )
    )
    store.append_event(
        RunEvent(
            record.run_id,
            "source_access_waiting",
            "Waiting for source access for Dice: dice.com requires a connected session.",
            phase="source_ingestion",
            current_source="Dice",
            counts={
                "source_index": 1,
                "source_count": 1,
                "source_id": "dice",
                "source_access_status": "needs_login",
                "source_action_href": "/sources/dice/session",
                "source_action_label": "Connect session",
            },
        )
    )

    items = runtime.active_work_payload()["sources"]

    assert len(items) == 2
    access_item = items[1]
    assert access_item["kind"] == "source_access"
    assert access_item["task_id"] == f"run-{record.run_id}-source-access-dice"
    assert access_item["status"] == "waiting"
    assert access_item["is_running"] is True
    assert access_item["title"] == "Dice needs source access"
    assert access_item["href"] == "/sources/dice/session"
    assert "Connect session" in access_item["message"]


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
    assert record.options["wait_for_source_access"] is True
    assert record.options["source_access_wait_timeout_seconds"] > 0
    assert submitted


def test_source_detail_launcher_can_include_seen_jobs(project_root: Path) -> None:
    runtime = WebRuntime(project_root)
    submitted = []
    runtime.executor.submit = lambda *args: submitted.append(args)

    record = runtime.launch_source_detail_run("sample-source", include_disabled_source=True, include_seen=True)

    assert record.options["include_seen"] is True
    assert record.options["mark_seen"] is True
    assert record.options["detail_extraction_limit"] is None
    assert record.options["wait_for_source_access"] is True
    assert submitted
    assert submitted[0][5] == "sample-source"
    assert submitted[0][6] is True


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
        jobs=[
            Job(title="SAP Basis Consultant", source=source.name, source_id=source.id, url="https://stale.example/1")
        ],
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
