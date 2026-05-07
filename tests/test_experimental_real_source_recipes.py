from __future__ import annotations

from pathlib import Path

from job_agent.services.extraction_quality import title_quality
from job_agent.services.job_board_recipe_service import extract_jobs_with_recipe, load_job_board_recipe

EXPERIMENTS = [
    (
        Path("sources/recipes/experimental/whitehall-sap-contract.yaml"),
        Path("tests/fixtures/real_sources/whitehall-sap-contract.html"),
        "https://www.whitehallresources.com/sap-jobs/contract/",
        {"SAP Integration Architect", "SAP RAR & Group Reporting Consultant"},
    ),
    (
        Path("sources/recipes/experimental/montreal-associates-jobs.yaml"),
        Path("tests/fixtures/real_sources/montreal-associates-jobs.html"),
        "https://www.montrealassociates.com/uk/candidates/job-search/",
        {"SAP ABAP Consultant", "SAP Fiori Developer"},
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
    }
    false_positive_url_parts = {
        "#job-application",
        "/services",
        "/contact",
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
    recipe = load_job_board_recipe(Path("sources/recipes/experimental/montreal-associates-jobs.yaml"))
    html = Path("tests/fixtures/real_sources/montreal-associates-jobs.html").read_text(encoding="utf-8")

    jobs = extract_jobs_with_recipe(
        html,
        "https://www.montrealassociates.com/uk/candidates/job-search/",
        recipe,
    )

    assert recipe.mode == "rendered_html"
    assert [job.title for job in jobs] == ["SAP ABAP Consultant", "SAP Fiori Developer"]
