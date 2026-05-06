from __future__ import annotations

import unittest

from job_agent.services.review_bundle_service import ReviewBundleService


class ReviewBundleServiceTests(unittest.TestCase):
    def test_builds_bundle_with_materials(self) -> None:
        bundle = ReviewBundleService().build(
            {
                "title": "SAP ABAP",
                "company": "Recruiter",
                "match_score": 82,
                "match_category": "strong",
                "recommended_angle": "Lead with ABAP",
            },
            {
                "cv": "cv text",
                "application": "app text",
                "form_answers": "forms",
                "match_analysis": "analysis",
                "job": "{}",
            },
            None,
        )
        self.assertIn("SAP ABAP", bundle)
        self.assertIn("cv text", bundle)
        self.assertIn("app text", bundle)
        self.assertIn("forms", bundle)

    def test_handles_missing_generated_files(self) -> None:
        bundle = ReviewBundleService().build({"title": "SAP ABAP"}, {}, None)
        self.assertIn("[Not generated yet]", bundle)


if __name__ == "__main__":
    unittest.main()
