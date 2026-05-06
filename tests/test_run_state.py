from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.digest import write_job_package
from job_agent.models import GeneratedPackage, Job, MatchResult
from job_agent.run_store import RunEvent, RunOptions, RunStore


class RunStateTests(unittest.TestCase):
    def test_run_registry_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root)
            record = store.create_run(RunOptions(include_seen=True))
            store.append_event(RunEvent(record.run_id, "run_started", "Started"))
            store.update(record.run_id, status="completed", total_loaded=2)

            loaded = store.get(record.run_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.status, "completed")
            self.assertEqual(store.read_events(record.run_id)[0]["event_type"], "run_started")

    def test_application_status_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStatusStore(Path(directory))
            record = store.ensure_for_job(
                stable_id="abc",
                fuzzy_key="fuzzy",
                title="SAP ABAP",
                company="Recruiter",
                source="Sample",
                url="https://example.com",
                application_url="https://example.com/apply",
            )
            self.assertEqual(record.status, "unreviewed")
            updated = store.update_status("abc", "applied", notes="Sent manually")
            self.assertEqual(updated.status, "applied")
            self.assertTrue(updated.applied_at)

    def test_package_index_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match = MatchResult(total_score=80, category="strong", recommended_angle="Lead with ABAP")
            package = GeneratedPackage("cv", "app", "forms", "analysis", [], [])
            paths = write_job_package(
                Job(title="SAP ABAP Consultant", company="Recruiter", url="https://example.com"),
                match,
                package,
                date(2026, 5, 6),
                root=root,
                run_id="run-1",
                stable_id="stable-1",
                fuzzy_key="fuzzy-1",
                state="new",
            )
            index = json.loads(Path(paths["index"]).read_text(encoding="utf-8"))
            self.assertEqual(index["run_id"], "run-1")
            self.assertEqual(index["stable_id"], "stable-1")

    def test_stale_running_run_is_marked_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root)
            record = store.create_run(RunOptions())
            store.update(record.run_id, status="running")
            recovered = store.recover_stale_runs()
            self.assertEqual(len(recovered), 1)
            self.assertEqual(store.get(record.run_id).status, "crashed")
            events = store.read_events(record.run_id)
            self.assertEqual(events[-1]["event_type"], "run_recovered_as_crashed")

    def test_archive_delete_restore_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            record = store.create_run(RunOptions())
            store.archive(record.run_id)
            self.assertEqual(store.list_runs(), [])
            self.assertEqual(len(store.list_runs(include_archived=True)), 1)
            store.soft_delete(record.run_id)
            self.assertEqual(store.list_runs(include_archived=True), [])
            self.assertEqual(len(store.list_runs(include_archived=True, include_deleted=True)), 1)
            store.restore(record.run_id)
            self.assertEqual(store.get(record.run_id).visibility, "active")

    def test_test_runs_can_be_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            store.create_run(RunOptions(is_test=True))
            store.create_run(RunOptions(is_test=False))
            self.assertEqual(len(store.list_runs(include_tests=False)), 1)
            self.assertEqual(len(store.list_runs(include_tests=True)), 2)


if __name__ == "__main__":
    unittest.main()
