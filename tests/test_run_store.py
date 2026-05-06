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
            store.append_event(RunEvent(record.run_id, "run_started", "Started"))
            store.update(record.run_id, status="running", total_loaded=3)
            self.assertEqual(store.read_events(record.run_id)[0]["event_type"], "run_started")

            recovered = store.recover_stale_runs()
            self.assertEqual(recovered[0].status, "crashed")
            self.assertEqual(store.get(record.run_id).status, "crashed")

            store.archive(record.run_id)
            self.assertEqual(store.list_runs(), [])
            store.soft_delete(record.run_id)
            self.assertEqual(store.list_runs(include_archived=True), [])
            store.restore(record.run_id)
            self.assertEqual(store.get(record.run_id).visibility, "active")


if __name__ == "__main__":
    unittest.main()
