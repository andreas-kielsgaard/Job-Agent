from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_agent.application_status_store import ApplicationStatusStore


class ApplicationStatusStoreTests(unittest.TestCase):
    def test_status_lifecycle_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStatusStore(Path(directory))
            record = store.ensure_for_job(
                stable_id="stable-1",
                fuzzy_key="fuzzy-1",
                title="SAP ABAP",
                company="Recruiter",
                source="Sample",
                url="https://example.com",
                application_url="https://example.com/apply",
            )
            self.assertEqual(record.status, "unreviewed")

            self.assertEqual(store.update_status("stable-1", "interesting").status, "interesting")
            not_interesting = store.update_status(
                "stable-1", "not_interesting", not_interesting_reason="Language mismatch"
            )
            self.assertEqual(not_interesting.not_interesting_reason, "Language mismatch")
            applied = store.update_status("stable-1", "applied")
            self.assertTrue(applied.applied_at)

            with self.assertRaises(ValueError):
                store.update_status("stable-1", "maybe")
            with self.assertRaises(KeyError):
                store.update_status("missing", "interesting")


if __name__ == "__main__":
    unittest.main()
