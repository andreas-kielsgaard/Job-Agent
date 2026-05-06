from __future__ import annotations

import unittest
from datetime import date

from job_agent.models import Job
from job_agent.scoring import score_job

PROFILE = {
    "location_policy": {"preferred_regions": ["Denmark", "Sweden", "Remote EU/UK"]},
    "skills": {
        "caveats": {
            "fiori": "Fiori caveat",
            "project_management": "PM caveat",
        }
    },
}


class ScoringTests(unittest.TestCase):
    def test_strong_abap_rap_match(self) -> None:
        job = Job(
            title="SAP ABAP RAP Consultant",
            location="Malmo, Sweden",
            remote="Hybrid",
            rate="EUR 850/day",
            posted_date="2026-05-04",
            deadline="2026-05-24",
            description="ABAP RAP CDS OData SAP Gateway S/4HANA Clean Core contract role.",
        )
        match = score_job(job, PROFILE, today=date(2026, 5, 6))
        self.assertEqual(match.category, "strong")
        self.assertGreaterEqual(match.total_score, 70)

    def test_language_mismatch_is_excluded(self) -> None:
        job = Job(
            title="SAP ABAP Entwickler",
            posted_date="2026-05-04",
            deadline="2026-05-24",
            description="ABAP BADI role. German required. English alone is not sufficient.",
        )
        match = score_job(job, PROFILE, today=date(2026, 5, 6))
        self.assertEqual(match.category, "excluded")
        self.assertIn("language", match.exclusion_reason.lower())

    def test_old_posting_is_excluded(self) -> None:
        job = Job(
            title="Old SAP ABAP Support",
            posted_date="2025-10-01",
            deadline="2025-10-20",
            description="ABAP debugging support.",
        )
        match = score_job(job, PROFILE, today=date(2026, 5, 6))
        self.assertEqual(match.category, "excluded")
        self.assertIn("older", match.exclusion_reason.lower())


if __name__ == "__main__":
    unittest.main()
