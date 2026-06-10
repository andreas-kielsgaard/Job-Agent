from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from job_agent.models import Job, MatchResult
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

    def test_includes_relevant_application_examples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "profile").mkdir()
            (root / "profile" / "application-examples.yaml").write_text(
                "application_examples:\n"
                "  - id: example-1\n"
                "    label: ABAP example\n"
                "    application_text: Human edited ABAP text.\n"
                "    linked_skills:\n"
                "      - ABAP\n",
                encoding="utf-8",
            )
            files = {
                "job": json.dumps(asdict(Job(title="SAP ABAP Consultant", description="ABAP"))),
                "match": json.dumps(asdict(MatchResult(80, "strong", matched_keywords=["ABAP"]))),
            }

            bundle = ReviewBundleService(root).build({"title": "SAP ABAP"}, files, None)

            self.assertIn("Human-edited Application Examples", bundle)
            self.assertIn("Human edited ABAP text.", bundle)


if __name__ == "__main__":
    unittest.main()
