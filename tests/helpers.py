from __future__ import annotations

from datetime import date
from pathlib import Path

from job_agent.digest import write_job_package
from job_agent.models import GeneratedPackage, Job, MatchResult


def write_sample_package(
    root: Path,
    *,
    run_id: str = "run-1",
    stable_id: str = "stable-1",
    title: str = "SAP ABAP Consultant",
    run_date: date = date(2026, 5, 6),
) -> dict[str, str]:
    return write_job_package(
        Job(
            title=title,
            company="Recruiter",
            url=f"https://example.com/{stable_id}",
            application_url=f"https://example.com/{stable_id}/apply",
        ),
        MatchResult(total_score=82, category="strong", recommended_angle="Lead with ABAP", concerns=["Confirm rate"]),
        GeneratedPackage("cv", "app", "forms", "analysis", [], []),
        run_date,
        root=root,
        run_id=run_id,
        stable_id=stable_id,
        fuzzy_key=f"fuzzy-{stable_id}",
        state="new",
    )
