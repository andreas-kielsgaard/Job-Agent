from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.services.extraction_quality import ExtractionQuality, candidate_quality
from job_agent.services.job_board_recipe_service import (
    AcceptRecipe,
    JobBoardRecipe,
    ListingRecipe,
    RejectRecipe,
    check_recipe_against_html,
    extract_jobs_with_recipe,
    load_job_board_recipe,
)
from job_agent.sources import extract_generic_jobs_from_html

FIXTURE_PATH = Path("tests/fixtures/synthetic-job-board.html")
HTML = FIXTURE_PATH.read_text(encoding="utf-8")


def test_recipe_extracts_real_job_cards_and_dedupes_urls() -> None:
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", _recipe())

    assert [job.title for job in jobs] == ["SAP ABAP Consultant", "SAP Basis Consultant"]
    assert jobs[0].url == "https://example.com/jobs/sap-abap"
    assert jobs[0].source_confidence == "recipe"
    assert jobs[0].freshness_confidence == "recipe"
    assert jobs[0].extraction_notes == ["Recipe-based extraction; verify details manually."]


def test_recipe_separates_title_selector_from_generic_link_selector() -> None:
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", _recipe())

    assert jobs[0].title == "SAP ABAP Consultant"
    assert jobs[0].url == "https://example.com/jobs/sap-abap"


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


def test_loader_validates_mode_and_positive_limits(tmp_path: Path) -> None:
    recipe_path = tmp_path / "bad-mode.yaml"
    recipe_path.write_text(
        "source_name: Bad\n"
        "mode: network_api\n"
        "listing:\n"
        "  card_selector: article\n"
        "  title_selector: h2\n"
        "  link_selector: a\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="mode"):
        load_job_board_recipe(recipe_path)

    recipe_path.write_text(
        "source_name: Bad\n"
        "listing:\n"
        "  card_selector: article\n"
        "  title_selector: h2\n"
        "  link_selector: a\n"
        "limits:\n"
        "  max_cards: 0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="max_cards"):
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
    assert recipe.mode == "static_html"
    assert recipe.listing.title_selector == [".job-heading", ".job-title", "h2"]


def test_cli_recipe_command_runs_against_local_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    from job_agent.cli import test_recipe

    test_recipe(
        "sources/recipes/examples/synthetic-job-board.yaml",
        str(FIXTURE_PATH),
        base_url="https://example.com/jobs",
    )

    output = capsys.readouterr().out
    assert "Jobs extracted: 2" in output
    assert "SAP ABAP Consultant" in output
    assert "https://example.com/jobs/sap-abap" in output


def _recipe(card_selector: str = ".job-card") -> JobBoardRecipe:
    return JobBoardRecipe(
        source_name="Test Board",
        listing=ListingRecipe(
            card_selector=card_selector,
            title_selector=[".job-heading", ".job-title", "h2"],
            link_selector=[".job-link", ".job-title"],
            company_selector=".company",
            location_selector=".location",
            rate_selector=".rate",
            posted_date_selector=".posted",
            description_selector=".summary",
        ),
        accept=AcceptRecipe(url_contains=["/jobs/"]),
        reject=RejectRecipe(
            title_exact=["Apply Now", "Services", "Job Search"],
            title_contains=["Job Search"],
            url_contains=["/services", "/about", "/contact"],
        ),
    )
