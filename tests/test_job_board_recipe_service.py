from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.services.extraction_quality import ExtractionQuality, candidate_quality
from job_agent.services.job_board_recipe_service import (
    JobBoardRecipe,
    ListingRecipe,
    RejectRecipe,
    check_recipe_against_html,
    extract_jobs_with_recipe,
    load_job_board_recipe,
)
from job_agent.sources import extract_generic_jobs_from_html

HTML = """
<html><body>
  <nav>
    <a href="/services">Services</a>
    <a href="/jobs">Job Search</a>
  </nav>
  <article class="job-card">
    <a class="job-title" href="/jobs/sap-abap">SAP ABAP Consultant</a>
    <span class="company">Client A</span>
    <span class="location">Copenhagen</span>
    <span class="rate">DKK 900/hour</span>
    <time class="posted">2026-05-07</time>
    <p class="summary">ABAP RAP CDS OData Gateway integration contract with hands-on delivery scope.</p>
  </article>
  <article class="job-card">
    <a class="job-title" href="/jobs/sap-basis">SAP Basis Consultant</a>
    <span class="company">Client B</span>
    <span class="location">Remote</span>
    <span class="rate">EUR 750/day</span>
    <time class="posted">2026-05-06</time>
    <p class="summary">Basis operations, upgrades, transport handling, and SAP landscape support.</p>
  </article>
  <article class="job-card">
    <a class="job-title" href="/jobs/sap-abap">SAP ABAP Consultant</a>
    <p class="summary">Duplicate card with the same URL.</p>
  </article>
  <article class="job-card">
    <a class="job-title" href="/jobs/sap-abap#apply">Apply Now</a>
    <p class="summary">CTA, not a job.</p>
  </article>
  <article class="job-card">
    <a class="job-title" href="/services">Services</a>
    <p class="summary">Category link, not a job.</p>
  </article>
</body></html>
"""


def test_recipe_extracts_real_job_cards_and_dedupes_urls() -> None:
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", _recipe())

    assert [job.title for job in jobs] == ["SAP ABAP Consultant", "SAP Basis Consultant"]
    assert jobs[0].url == "https://example.com/jobs/sap-abap"
    assert jobs[0].source_confidence == "recipe"
    assert jobs[0].freshness_confidence == "recipe"
    assert jobs[0].extraction_notes == ["Recipe-based extraction; verify details manually."]


def test_recipe_extracts_optional_location_rate_and_date_fields() -> None:
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", _recipe())

    assert jobs[0].company == "Client A"
    assert jobs[0].location == "Copenhagen"
    assert jobs[0].rate == "DKK 900/hour"
    assert jobs[0].posted_date == "2026-05-07"


def test_recipe_rejects_cta_services_and_category_links() -> None:
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", _recipe())
    titles = [job.title for job in jobs]

    assert "Apply Now" not in titles
    assert "Services" not in titles
    assert all("/services" not in job.url for job in jobs)


def test_loader_validates_missing_required_selectors(tmp_path: Path) -> None:
    recipe_path = tmp_path / "bad.yaml"
    recipe_path.write_text(
        "source_name: Bad\nlisting:\n  card_selector: article\n  title_selector: a\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="link_selector"):
        load_job_board_recipe(recipe_path)


def test_empty_card_selector_returns_empty_quality_warning() -> None:
    quality = check_recipe_against_html(HTML, "https://example.com", _recipe(card_selector=".missing"))

    assert quality.candidate_count == 0
    assert quality.warnings == ["Recipe extraction found no matching job cards."]


def test_quality_check_rates_recipe_output_better_than_generic_false_positive_html() -> None:
    generic_jobs = extract_generic_jobs_from_html(HTML, "https://example.com", "Generic")
    generic_quality = ExtractionQuality(label="Generic")
    generic_quality.candidates = [candidate_quality(job) for job in generic_jobs]

    recipe_quality = check_recipe_against_html(HTML, "https://example.com", _recipe())

    assert generic_quality.candidate_count > recipe_quality.candidate_count
    assert recipe_quality.generic_title_count == 0
    assert recipe_quality.unique_url_count == 2
    assert all(
        candidate.title not in {"Services", "Job Search", "Apply Now"} for candidate in recipe_quality.candidates
    )


def test_example_recipe_loads() -> None:
    recipe = load_job_board_recipe(Path("sources/recipes/examples/synthetic-job-board.yaml"))

    assert recipe.source_name == "Synthetic Example Job Board"
    assert recipe.listing.card_selector == ".job-card"


def _recipe(card_selector: str = ".job-card") -> JobBoardRecipe:
    return JobBoardRecipe(
        source_name="Test Board",
        listing=ListingRecipe(
            card_selector=card_selector,
            title_selector=".job-title",
            link_selector=".job-title",
            company_selector=".company",
            location_selector=".location",
            rate_selector=".rate",
            posted_date_selector=".posted",
            description_selector=".summary",
        ),
        reject=RejectRecipe(
            title_exact=["Apply Now", "Services"],
            title_contains=["Job Search"],
            url_contains=["/services", "/about", "/contact"],
        ),
    )
