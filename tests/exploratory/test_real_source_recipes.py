from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.services.extraction_quality import title_quality
from job_agent.services.job_board_recipe_service import extract_jobs_with_recipe, load_job_board_recipe

pytestmark = pytest.mark.exploratory

EXPERIMENTS = [
    (
        Path("tests/fixtures/recipes/experimental/eursap-jobs.yaml"),
        Path("tests/fixtures/real_sources/eursap-jobs.html"),
        "https://eursap.eu/jobs",
        {"SAP Basis Consultant", "SAP Commerce (Hybris) Developer"},
    ),
    (
        Path("tests/fixtures/recipes/experimental/whitehall-sap-contract.yaml"),
        Path("tests/fixtures/real_sources/whitehall-sap-contract.html"),
        "https://www.whitehallresources.com/sap-jobs/contract/",
        {"SAP Integration Architect", "SAP SD Consultant \u2013 Italian Speaking", "SAP ABAP Developer \u2013 S/4HANA"},
    ),
    (
        Path("tests/fixtures/recipes/experimental/montreal-associates-jobs.yaml"),
        Path("tests/fixtures/real_sources/montreal-associates-jobs.html"),
        "https://www.montrealassociates.com/uk/candidates/job-search/",
        {"SAP ABAP Consultant", "Business Analyst SAP CO (S4)"},
    ),
]


def test_experimental_real_source_recipes_extract_expected_jobs() -> None:
    for recipe_path, fixture_path, base_url, expected_titles in EXPERIMENTS:
        recipe = load_job_board_recipe(recipe_path)
        html = fixture_path.read_text(encoding="utf-8")

        jobs = extract_jobs_with_recipe(html, base_url, recipe)

        assert {job.title for job in jobs} == expected_titles
        assert len({job.url for job in jobs}) == len(jobs)
        assert all(title_quality(job.title) == "useful" for job in jobs)
        assert all(job.source_confidence == "recipe" for job in jobs)


def test_experimental_recipes_reject_known_false_positive_labels_and_urls() -> None:
    false_positive_titles = {
        "Apply Now",
        "Services",
        "Job Search",
        "SuccessFactors",
        "Contract Staffing",
        "Work with MA",
        "Upload SAP Job",
        "Improve my CV",
        "Promote my CV",
        "SAP Talent",
    }
    false_positive_url_parts = {
        "#job-application",
        "/hire-sap-talent",
        "/services",
        "/contact",
        "/blog",
        "/countries",
        "/sap-jobs/?",
        "/staffing-solutions/",
        "/candidates/job-search/#",
    }

    for recipe_path, fixture_path, base_url, _expected_titles in EXPERIMENTS:
        recipe = load_job_board_recipe(recipe_path)
        html = fixture_path.read_text(encoding="utf-8")

        jobs = extract_jobs_with_recipe(html, base_url, recipe)

        assert not false_positive_titles.intersection({job.title for job in jobs})
        assert all(not any(fragment in job.url for fragment in false_positive_url_parts) for job in jobs)


def test_rendered_experimental_recipe_runs_against_fixture_without_playwright() -> None:
    recipe = load_job_board_recipe(Path("tests/fixtures/recipes/experimental/montreal-associates-jobs.yaml"))
    html = Path("tests/fixtures/real_sources/montreal-associates-jobs.html").read_text(encoding="utf-8")

    jobs = extract_jobs_with_recipe(
        html,
        "https://www.montrealassociates.com/uk/candidates/job-search/",
        recipe,
    )

    assert recipe.mode == "rendered_html"
    assert [job.title for job in jobs] == ["SAP ABAP Consultant", "Business Analyst SAP CO (S4)"]


def test_eursap_pattern_extraction_returns_clean_fields() -> None:
    recipe = load_job_board_recipe(Path("tests/fixtures/recipes/experimental/eursap-jobs.yaml"))
    html = Path("tests/fixtures/real_sources/eursap-jobs.html").read_text(encoding="utf-8")

    jobs = extract_jobs_with_recipe(html, "https://eursap.eu/jobs", recipe)

    assert jobs[0].title == "SAP Basis Consultant"
    assert jobs[0].url == "https://eursap.eu/jobs/sap-basis-consultant-34235-remote"
    assert jobs[0].location == "Remote Work"
    assert jobs[0].languages == ["English"]
    assert jobs[0].start_date == "Sep 01, 2026"
    assert jobs[0].workload == "Permanent"
    assert jobs[0].rate == "EUR 48k - EUR 85k/annum (depending on experience)"
    assert "Recipe extracted job ID: 34235" in jobs[0].extraction_notes


def test_whitehall_rejects_apply_anchor_and_extracts_compact_block_fields() -> None:
    recipe = load_job_board_recipe(Path("tests/fixtures/recipes/experimental/whitehall-sap-contract.yaml"))
    html = Path("tests/fixtures/real_sources/whitehall-sap-contract.html").read_text(encoding="utf-8")

    jobs = extract_jobs_with_recipe(html, "https://www.whitehallresources.com/sap-jobs/contract/", recipe)

    assert "View Job" not in {job.title for job in jobs}
    assert "Apply Now" not in {job.title for job in jobs}
    assert all("#job-application" not in job.url for job in jobs)
    remote_job = next(job for job in jobs if job.title == "SAP SD Consultant \u2013 Italian Speaking")
    assert remote_job.location == "Anywhere"
    assert remote_job.remote == "Remote"
    assert remote_job.workload == "Contract"
    assert "Recipe extracted job ID: BBBH66783_1778067511" in remote_job.extraction_notes
