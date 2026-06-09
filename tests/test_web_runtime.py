from __future__ import annotations

from pathlib import Path

from job_agent.io.yaml_store import read_yaml
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
    assert active["progress_percent"] == 100
    assert active["href"] == "/sources/sample-source/session"


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
