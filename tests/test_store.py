from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from job_agent.models import Job
from job_agent.store import JobStore


class StoreTests(unittest.TestCase):
    def test_classifies_new_then_previously_seen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = Job(title="SAP ABAP Consultant", company="Recruiter", url="https://example.com/job")
            first = store.classify([job], today=date(2026, 5, 6))[0]
            self.assertEqual(first.status, "new")
            store.mark_seen([first])
            second = store.classify([job], today=date(2026, 5, 7))[0]
            self.assertEqual(second.status, "previously_seen")

    def test_changed_content_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = Job(
                title="SAP ABAP Consultant", company="Recruiter", url="https://example.com/job", description="ABAP"
            )
            first = store.classify([job], today=date(2026, 5, 6))[0]
            store.mark_seen([first])
            changed = Job(
                title="SAP ABAP Consultant", company="Recruiter", url="https://example.com/job", description="ABAP RAP"
            )
            state = store.classify([changed], today=date(2026, 5, 7))[0]
            self.assertEqual(state.status, "changed")


if __name__ == "__main__":
    unittest.main()
