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

    def test_remote_required_excludes_non_remote_role(self) -> None:
        profile = {**PROFILE, "match_engine": {"remote_policy": "required"}}
        job = Job(
            title="SAP ABAP RAP Consultant",
            location="Copenhagen",
            remote="Onsite",
            posted_date="2026-05-04",
            description="ABAP RAP CDS OData Gateway contract role.",
        )

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertEqual(match.category, "excluded")
        self.assertIn("remote", match.exclusion_reason.lower())

    def test_required_keyword_group_excludes_when_missing(self) -> None:
        profile = {
            **PROFILE,
            "match_engine": {
                "technical_keyword_groups": [
                    {"label": "ABAP variants", "terms": ["abap", "sap abap"], "score": 40, "mode": "required"}
                ]
            },
        }
        job = Job(
            title="SAP Technical Consultant",
            remote="Remote",
            posted_date="2026-05-04",
            description="OData Gateway CDS contract role.",
        )

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertEqual(match.category, "excluded")
        self.assertIn("ABAP variants", match.exclusion_reason)

    def test_permanent_role_is_penalized_by_default(self) -> None:
        job = Job(
            title="SAP ABAP Senior Developer",
            remote="Remote",
            workload="Permanent",
            posted_date="2026-05-04",
            description="ABAP CDS OData Gateway role.",
        )

        match = score_job(job, PROFILE, today=date(2026, 5, 6))

        self.assertEqual(match.components["contract_fit"], -25)
        self.assertNotEqual(match.category, "strong")


if __name__ == "__main__":
    unittest.main()
