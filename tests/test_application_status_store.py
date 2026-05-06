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
            again = store.ensure_for_job(
                stable_id="stable-1",
                fuzzy_key="changed",
                title="Changed",
                company="Changed",
                source="Changed",
                url="https://changed.example",
                application_url="https://changed.example/apply",
            )
            self.assertEqual(again.title, "SAP ABAP")

            self.assertEqual(store.update_status("stable-1", "interesting").status, "interesting")
            not_interesting = store.update_status(
                "stable-1", "not_interesting", not_interesting_reason="Language mismatch"
            )
            self.assertEqual(not_interesting.not_interesting_reason, "Language mismatch")
            applied = store.update_status("stable-1", "applied")
            self.assertTrue(applied.applied_at)
            applied_again = store.update_status("stable-1", "applied")
            self.assertEqual(applied_again.applied_at, applied.applied_at)

            with self.assertRaises(ValueError):
                store.update_status("stable-1", "maybe")
            with self.assertRaises(KeyError):
                store.update_status("missing", "interesting")

    def test_corrupt_status_store_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationStatusStore(Path(directory))
            store.path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.list_all()


if __name__ == "__main__":
    unittest.main()
