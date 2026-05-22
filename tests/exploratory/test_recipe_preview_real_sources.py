from __future__ import annotations

import pytest

from job_agent.services.recipe_preview_service import preview_recipe

pytestmark = pytest.mark.exploratory


def test_preview_result_includes_jobs_and_quality_summary() -> None:
    result = preview_recipe(
        "tests/fixtures/recipes/experimental/eursap-jobs.yaml",
        "tests/fixtures/real_sources/eursap-jobs.html",
        base_url="https://eursap.eu/jobs",
    )

    assert result.recipe_source_name == "Eursap Jobs (experimental)"
    assert result.recipe_status == "experimental"
    assert result.input_type == "local fixture"
    assert result.mode_used == "local_fixture_html"
    assert result.extracted_job_count == 2
    assert result.useful_titles == 2
    assert result.generic_labels == 0
    assert result.unique_urls == 2
    assert result.average_description_length > 0
    assert [job.title for job in result.jobs] == ["SAP Basis Consultant", "SAP Commerce (Hybris) Developer"]


def test_preview_includes_eursap_pattern_fields() -> None:
    result = preview_recipe(
        "tests/fixtures/recipes/experimental/eursap-jobs.yaml",
        "tests/fixtures/real_sources/eursap-jobs.html",
        base_url="https://eursap.eu/jobs",
    )

    job = result.jobs[0]
    assert job.location == "Remote Work"
    assert job.languages == ["English"]
    assert job.start_date == "Sep 01, 2026"
    assert job.workload == "Permanent"
    assert job.rate == "EUR 48k - EUR 85k/annum (depending on experience)"
    assert "Recipe extracted job ID: 34235" in job.extraction_notes


def test_preview_includes_whitehall_fields_and_rejects_application_anchors() -> None:
    result = preview_recipe(
        "tests/fixtures/recipes/experimental/whitehall-sap-contract.yaml",
        "tests/fixtures/real_sources/whitehall-sap-contract.html",
        base_url="https://www.whitehallresources.com/sap-jobs/contract/",
    )

    assert result.extracted_job_count == 3
    assert all("#job-application" not in job.url for job in result.jobs)
    remote_job = next(job for job in result.jobs if job.title == "SAP SD Consultant \u2013 Italian Speaking")
    assert remote_job.location == "Anywhere"
    assert remote_job.remote == "Remote"
    assert remote_job.workload == "Contract"
    assert "Recipe extracted job ID: BBBH66783_1778067511" in remote_job.extraction_notes


def test_preview_handles_local_rendered_recipe_warning() -> None:
    result = preview_recipe(
        "tests/fixtures/recipes/experimental/montreal-associates-jobs.yaml",
        "tests/fixtures/real_sources/montreal-associates-jobs.html",
        base_url="https://www.montrealassociates.com/uk/candidates/job-search/",
    )

    assert result.extracted_job_count == 2
    assert "Local fixture HTML ignores recipe mode: rendered_html." in result.warnings
