from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_agent.run_store import RunEvent, RunOptions, RunStore


class RunStoreBoundaryTests(unittest.TestCase):
    def test_run_lifecycle_events_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            record = store.create_run(RunOptions(include_seen=True))
            self.assertTrue(Path(record.run_log_path).parent.exists())
            self.assertTrue(record.events_path.endswith("events.jsonl"))
            store.append_event(RunEvent(record.run_id, "run_started", "Started"))
            store.append_event(RunEvent(record.run_id, "run_progress", "Progress"))
            store.update(record.run_id, status="running", total_loaded=3)
            self.assertEqual(store.read_events(record.run_id)[0]["event_type"], "run_started")
            self.assertEqual(len(store.read_events(record.run_id, limit=1)), 1)
            self.assertIn("Started", Path(record.run_log_path).read_text(encoding="utf-8"))

            recovered = store.recover_stale_runs()
            self.assertEqual(recovered[0].status, "crashed")
            self.assertEqual(store.get(record.run_id).status, "crashed")

            store.archive(record.run_id)
            self.assertEqual(store.list_runs(), [])
            store.soft_delete(record.run_id)
            self.assertEqual(store.list_runs(include_archived=True), [])
            store.restore(record.run_id)
            self.assertEqual(store.get(record.run_id).visibility, "active")

            with self.assertRaises(KeyError):
                store.update("missing", status="completed")

    def test_corrupt_registry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root)
            store.registry_path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.list_runs()

    def test_corrupt_registry_can_be_backed_up_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root)
            store.registry_path.write_text("{not-json", encoding="utf-8")

            backup = store.recover_corrupt_registry()

            self.assertIsNotNone(backup)
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(encoding="utf-8"), "{not-json")
            self.assertEqual(store.list_runs(), [])


if __name__ == "__main__":
    unittest.main()
