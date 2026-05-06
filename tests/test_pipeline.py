from __future__ import annotations

import unittest
from datetime import date

from job_agent.config import ROOT, load_profile
from job_agent.generator import generate_materials
from job_agent.scoring import score_job
from job_agent.sources import load_jobs_from_sources


class PipelineTests(unittest.TestCase):
    def test_sample_pipeline_renders_recruiter_safe_cv(self) -> None:
        profile = load_profile(ROOT)
        source_result = load_jobs_from_sources(ROOT)
        job = source_result.jobs[0]
        match = score_job(job, profile, today=date(2026, 5, 6))
        package = generate_materials(job, match, profile, use_llm=False, root=ROOT)
        self.assertIn(job.title, package.cv)
        self.assertNotIn("match score", package.cv.lower())
        self.assertIn("Standard Form Answer Package", package.form_answers)


if __name__ == "__main__":
    unittest.main()
