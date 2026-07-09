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

    def test_remote_required_does_not_exclude_unknown_remote_setup(self) -> None:
        profile = {**PROFILE, "match_engine": {**SAP_MATCH_ENGINE, "remote_policy": "required"}}
        job = Job(
            title="SAP ABAP RAP Consultant",
            description="ABAP RAP CDS OData Gateway contract role.",
        )

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertNotEqual(match.category, "excluded")
        self.assertNotIn("remote", match.exclusion_reason.lower())

    def test_missing_freshness_and_rate_are_not_negative_match_signals(self) -> None:
        job = Job(
            title="SAP ABAP RAP Consultant",
            remote="Remote",
            description="ABAP RAP CDS OData Gateway contract role.",
        )

        match = score_job(job, PROFILE, today=date(2026, 5, 6))

        self.assertEqual(match.components["freshness_risk"], 0)
        self.assertEqual(match.components["rate_visibility_or_rate_fit"], 0)
        self.assertNotIn(
            "Freshness is uncertain because no reliable posting date or deadline was found.",
            match.concerns,
        )
        self.assertNotIn("Rate or salary is not listed.", match.concerns)

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

    def test_unified_keyword_groups_average_main_and_apply_highest_bonus_and_detractor(self) -> None:
        profile = {
            "match_engine": {
                "keyword_groups": [
                    {
                        "label": "ABAP variants",
                        "terms": ["abap", "sap abap"],
                        "proficiency": 100,
                        "mode": "main",
                        "years": 6,
                    },
                    {"label": "RAP", "terms": ["rap"], "proficiency": 80, "mode": "main"},
                    {"label": "Clean core", "terms": ["clean core"], "proficiency": 25, "mode": "bonus"},
                    {"label": "Gateway", "terms": ["gateway"], "proficiency": 10, "mode": "bonus"},
                    {"label": "Pure UI5", "terms": ["ui5"], "proficiency": -15, "mode": "detractor"},
                ]
            },
            "language_policy": {"acceptable": [], "fluent": []},
        }
        job = Job(
            title="SAP ABAP RAP Consultant",
            description="ABAP RAP Gateway Clean Core with UI5 exposure. Requires 8 years experience.",
        )

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertEqual(match.components["main_proficiency"], 90)
        self.assertEqual(match.components["bonus_boost"], 25)
        self.assertEqual(match.components["detractor_penalty"], -15)
        self.assertEqual(match.total_score, 100)
        self.assertIn("ABAP variants", match.matched_keywords)
        self.assertTrue(any("8+ years" in concern for concern in match.concerns))

    def test_employment_conditions_flag_without_changing_proficiency_score(self) -> None:
        profile = {
            "match_engine": {
                "keyword_groups": [{"label": "ABAP variants", "terms": ["abap"], "proficiency": 100, "mode": "main"}]
            },
            "employment_conditions": {
                "employment_type": {"contract": "required", "employed": "excluded"},
                "remote": {"remote": "preferred", "onsite": "excluded"},
                "locations": [{"label": "Copenhagen", "kind": "city", "preference": "preferred"}],
            },
            "language_policy": {"acceptable": [], "fluent": []},
        }
        job = Job(
            title="SAP ABAP Developer",
            location="Berlin",
            remote="On-site",
            workload="Permanent",
            description="ABAP role for a permanent employee.",
        )

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertEqual(match.total_score, 100)
        self.assertEqual(match.category, "strong")
        self.assertTrue(match.condition_exclusions)
        self.assertTrue(match.condition_preferences)

    def test_employment_preferences_do_not_become_scoring_concerns(self) -> None:
        profile = {
            "match_engine": {
                "keyword_groups": [{"label": "ABAP variants", "terms": ["abap"], "proficiency": 100, "mode": "main"}]
            },
            "employment_conditions": {
                "remote": {"remote": "preferred"},
                "locations": [{"label": "Copenhagen", "kind": "city", "preference": "preferred"}],
            },
            "language_policy": {"acceptable": [], "fluent": []},
        }
        job = Job(
            title="SAP ABAP Developer",
            location="Berlin",
            remote="Onsite",
            description="ABAP role.",
            source_confidence="high",
        )

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertEqual(match.total_score, 100)
        self.assertEqual(match.deterministic_confidence, "high")
        self.assertEqual(match.concerns, [])
        self.assertTrue(match.condition_preferences)

    def test_remote_condition_prefers_hybrid_over_remote_wording(self) -> None:
        profile = {
            "match_engine": {
                "keyword_groups": [{"label": "ABAP variants", "terms": ["abap"], "proficiency": 100, "mode": "main"}]
            },
            "employment_conditions": {"remote": {"hybrid": "required", "remote": "excluded"}},
            "language_policy": {"acceptable": [], "fluent": []},
        }
        job = Job(title="SAP ABAP Developer", remote="Hybrid remote", description="ABAP role.")

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertEqual(match.condition_values["remote"], "hybrid")
        self.assertEqual(match.condition_exclusions, [])

    def test_required_eu_location_matches_remote_region_text(self) -> None:
        profile = {
            "match_engine": {
                "keyword_groups": [{"label": "ABAP variants", "terms": ["abap"], "proficiency": 100, "mode": "main"}]
            },
            "employment_conditions": {
                "locations": [{"label": "EU", "kind": "region", "preference": "required"}],
            },
            "language_policy": {"acceptable": [], "fluent": []},
        }
        job = Job(title="SAP ABAP Developer", location="Not listed", remote="Remote EU/UK", description="ABAP role.")

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertEqual(match.condition_values["locations"], ["EU"])
        self.assertEqual(match.condition_exclusions, [])

    def test_preferred_location_tags_match_any_configured_preference(self) -> None:
        profile = {
            "match_engine": {
                "keyword_groups": [{"label": "ABAP variants", "terms": ["abap"], "proficiency": 100, "mode": "main"}]
            },
            "employment_conditions": {
                "locations": [
                    {"label": "Copenhagen", "kind": "city", "preference": "preferred"},
                    {"label": "Aarhus", "kind": "city", "preference": "preferred"},
                ],
            },
            "language_policy": {"acceptable": [], "fluent": []},
        }
        job = Job(title="SAP ABAP Developer", location="Copenhagen", description="ABAP role.")

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertFalse(any("Aarhus" in concern for concern in match.condition_preferences))
        self.assertFalse(any("Location preference" in concern for concern in match.condition_preferences))

    def test_not_preferred_language_is_not_an_employment_condition_exclusion(self) -> None:
        profile = {
            "match_engine": {
                "keyword_groups": [{"label": "ABAP variants", "terms": ["abap"], "proficiency": 100, "mode": "main"}]
            },
            "employment_conditions": {
                "languages": [{"label": "English", "preference": "not_preferred"}],
            },
            "language_policy": {"acceptable": [], "fluent": []},
        }
        job = Job(title="SAP ABAP Developer", description="ABAP role. English required.")

        match = score_job(job, profile, today=date(2026, 5, 6))

        self.assertEqual(match.condition_exclusions, [])
        self.assertTrue(any("not-preferred language" in concern for concern in match.condition_preferences))


if __name__ == "__main__":
    unittest.main()
