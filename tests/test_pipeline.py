from __future__ import annotations

from datetime import date
from pathlib import Path

from job_agent.config import load_profile
from job_agent.generator import generate_materials
from job_agent.scoring import score_job
from job_agent.sources import iter_source_results


def test_sample_pipeline_renders_recruiter_safe_cv(local_yaml_source_project: Path) -> None:
    profile = load_profile(local_yaml_source_project)
    source_result = next(iter_source_results(local_yaml_source_project)).result
    job = source_result.jobs[0]
    match = score_job(job, profile, today=date(2026, 5, 6))
    package = generate_materials(job, match, profile, use_llm=False, root=local_yaml_source_project)

    assert job.title in package.cv
    assert "match score" not in package.cv.lower()
    assert "Standard Form Answer Package" in package.form_answers
