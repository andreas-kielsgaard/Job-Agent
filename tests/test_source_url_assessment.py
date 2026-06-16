from __future__ import annotations

from job_agent.services.source_url_assessment import assess_source_setup_url


def test_source_url_assessment_blocks_root_homepage_for_auto_setup() -> None:
    assessment = assess_source_setup_url("https://example.com")

    assert assessment.can_auto_setup is False
    assert assessment.status == "needs_listing_url"
    assert "jobs" in assessment.message


def test_source_url_assessment_allows_job_listing_paths_and_queries() -> None:
    assert assess_source_setup_url("https://example.com/jobs").can_auto_setup is True
    assert assess_source_setup_url("https://example.com/en-gb/job-search/?industry=SAP").can_auto_setup is True
