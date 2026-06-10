from __future__ import annotations

import unittest
from datetime import date

from job_agent.models import Job
from job_agent.scoring import score_job

SAP_MATCH_ENGINE = {
    "remote_policy": "slight_preference",
    "permanent_policy": "penalize",
    "permanent_penalty": -25,
    "technical_cap": 55,
    "module_cap": 25,
    "technical_keyword_groups": [
        {"label": "ABAP core", "terms": ["abap", "sap abap", "abap oo"], "score": 22, "mode": "bonus"},
        {"label": "RAP", "terms": ["rap", "restful application programming"], "score": 12, "mode": "bonus"},
        {"label": "CDS", "terms": ["cds", "cds views"], "score": 10, "mode": "bonus"},
        {"label": "OData / Gateway", "terms": ["odata", "gateway", "sap gateway"], "score": 10, "mode": "bonus"},
        {"label": "S/4HANA or ECC", "terms": ["s/4hana", "s4hana", "ecc"], "score": 8, "mode": "bonus"},
    ],
    "module_keyword_groups": [],
    "contract_keyword_groups": [
        {"label": "Contract / freelance", "terms": ["contract", "freelance"], "score": 8, "mode": "bonus"}
    ],
}

PROFILE = {
    "location_policy": {"preferred_regions": ["Denmark", "Sweden", "Remote EU/UK"]},
    "skills": {
        "caveats": {
            "fiori": "Fiori caveat",
            "project_management": "PM caveat",
        }
    },
    "match_engine": SAP_MATCH_ENGINE,
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
        profile = {**PROFILE, "match_engine": {**SAP_MATCH_ENGINE, "remote_policy": "required"}}
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

    def test_fiori_adds_review_trigger_without_hidden_penalty(self) -> None:
        job = Job(
            title="SAP Fiori Backend Developer",
            remote="Remote",
            posted_date="2026-05-04",
            description="Fiori UI5 with ABAP Gateway backend.",
        )

        match = score_job(job, PROFILE, today=date(2026, 5, 6))

        self.assertIn("fiori_ui5_depth", match.review_triggers)
        self.assertIn("Fiori caveat", match.concerns)
        self.assertNotIn("frontend_or_functional_risk", match.components)

    def test_configured_language_policy_controls_mandatory_language_exclusion(self) -> None:
        profile = {
            **PROFILE,
            "language_policy": {
                "acceptable": ["English"],
                "fluent": ["Danish"],
                "exclude_if_mandatory_unmatched": True,
            },
        }
        job = Job(
            title="Consultant",
            posted_date="2026-05-04",
            description="German required for stakeholder workshops.",
        )

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertEqual(match.category, "excluded")
        self.assertIn("language", match.exclusion_reason.lower())

    def test_neutral_language_policy_does_not_inherit_legacy_language_assumptions(self) -> None:
        profile = {**PROFILE, "language_policy": {"acceptable": [], "fluent": []}}
        job = Job(
            title="Consultant",
            posted_date="2026-05-04",
            description="German required for stakeholder workshops.",
        )

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertNotEqual(match.category, "excluded")

    def test_target_role_aliases_add_role_interest_fit(self) -> None:
        profile = {
            "target_roles": {"high_match": ["Platform Engineer"]},
            "target_role_aliases": {"Platform Engineer": ["Integration developer"]},
            "match_engine": {},
            "language_policy": {"acceptable": [], "fluent": []},
        }
        job = Job(title="Integration Developer", description="Builds internal tools.")

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertEqual(match.components["role_interest_fit"], 8)


if __name__ == "__main__":
    unittest.main()
