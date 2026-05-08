from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.services.job_board_recipe_service import extract_jobs_with_recipe, job_board_recipe_from_mapping
from job_agent.services.recipe_calibration_service import (
    audit_recipe_selectors,
    capture_recipe_calibration,
    discover_candidate_elements,
)

CALIBRATION_HTML = """
<!doctype html>
<html><body>
  <nav><a href="/services">Services</a><a href="/jobs">Job Search</a></nav>
  <section class="job-list">
    <article class="job-card">
      <h2><a href="/jobs/sap-abap">SAP ABAP Consultant</a></h2>
      <p>Contract role with RAP, CDS, OData and Gateway work. Remote option.</p>
    </article>
    <section class="heading-block">
      <h3>SAP Basis Consultant</h3>
      <a href="/jobs/sap-basis">View job</a>
      <span>Hybrid contract in Copenhagen</span>
    </section>
    <table><tbody>
      <tr class="project-row">
        <td><a href="/projects/sap-fiori">SAP Fiori project</a></td>
        <td>SAP</td>
        <td>Aarhus</td>
        <td>Deadline 2026-05-30</td>
      </tr>
    </tbody></table>
    <a class="blob" href="/jobs/sap-rap">SAP RAP Developer Job ID: 12345 Location: Remote Language: English Start date: ASAP Work type: Contract Pay: EUR 700/day</a>
  </section>
</body></html>
"""


def test_candidate_discovery_finds_job_ancestors_heading_rows_and_single_link_blobs() -> None:
    candidates = discover_candidate_elements(CALIBRATION_HTML, max_candidates=20)
    kinds = {candidate.kind for candidate in candidates}

    assert "card" in kinds
    assert "heading_block" in kinds
    assert "table_row" in kinds
    assert "single_link_blob" in kinds
    assert any(candidate.contains_sap_terms for candidate in candidates)


def test_candidate_discovery_marks_navigation_noise() -> None:
    candidates = discover_candidate_elements(CALIBRATION_HTML, max_candidates=20)

    assert any(candidate.likely_noise for candidate in candidates)


def test_selector_audit_reports_zero_card_matches() -> None:
    recipe = job_board_recipe_from_mapping(
        {
            "source_name": "Bad",
            "listing": {"card_selector": ".missing", "title_selector": "h2", "link_selector": "a"},
        }
    )

    audit = audit_recipe_selectors(CALIBRATION_HTML, "https://example.com/jobs", recipe)

    assert audit.card_match_count == 0
    assert "listing.card_selector matched 0 elements." in audit.warnings


def test_selector_audit_reports_title_and_link_failures_inside_cards() -> None:
    recipe = job_board_recipe_from_mapping(
        {
            "source_name": "Bad",
            "listing": {
                "card_selector": ".job-card",
                "title_selector": ".missing-title",
                "link_selector": ".missing-link",
            },
        }
    )

    audit = audit_recipe_selectors(CALIBRATION_HTML, "https://example.com/jobs", recipe)

    assert audit.card_match_count == 1
    assert "title_selector matched 0 elements inside first cards." in audit.warnings
    assert "link_selector matched 0 elements inside first cards." in audit.warnings


def test_pattern_extraction_parses_eursap_style_text_blob() -> None:
    recipe = job_board_recipe_from_mapping(
        {
            "source_name": "Eursap Pattern",
            "listing": {"card_selector": ".blob", "title_selector": ".blob", "link_selector": ".blob"},
            "accept": {"url_contains": ["/jobs/"]},
            "patterns": {
                "title_regex": r"^(?P<title>SAP RAP Developer)",
                "job_id_regex": r"Job ID:\s*(?P<job_id>\d+)",
                "location_regex": r"Location:\s*(?P<location>[^:]+?)\s+Language:",
                "language_regex": r"Language:\s*(?P<language>[^:]+?)\s+Start date:",
                "start_date_regex": r"Start date:\s*(?P<start_date>[^:]+?)\s+Work type:",
                "work_type_regex": r"Work type:\s*(?P<work_type>[^:]+?)\s+Pay:",
                "rate_regex": r"Pay:\s*(?P<rate>.+)$",
            },
        }
    )

    jobs = extract_jobs_with_recipe(CALIBRATION_HTML, "https://example.com", recipe)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "SAP RAP Developer"
    assert job.location == "Remote"
    assert job.languages == ["English"]
    assert job.start_date == "ASAP"
    assert job.workload == "Contract"
    assert job.rate == "EUR 700/day"
    assert "Recipe extracted job ID: 12345" in job.extraction_notes


def test_invalid_pattern_regex_fails_validation() -> None:
    with pytest.raises(ValueError, match="patterns.title_regex"):
        job_board_recipe_from_mapping(
            {
                "source_name": "Bad",
                "listing": {"card_selector": ".blob", "title_selector": ".blob", "link_selector": ".blob"},
                "patterns": {"title_regex": "("},
            }
        )


def test_table_like_fixture_extracts_project_rows() -> None:
    recipe = job_board_recipe_from_mapping(
        {
            "source_name": "Accuro-like Table",
            "listing": {
                "card_selector": "tr.project-row",
                "title_selector": "td:nth-of-type(1) a",
                "link_selector": "td:nth-of-type(1) a",
                "company_selector": "td:nth-of-type(2)",
                "location_selector": "td:nth-of-type(3)",
                "posted_date_selector": "td:nth-of-type(4)",
            },
            "accept": {"url_contains": ["/projects/"]},
        }
    )

    jobs = extract_jobs_with_recipe(CALIBRATION_HTML, "https://example.com", recipe)

    assert len(jobs) == 1
    assert jobs[0].title == "SAP Fiori project"
    assert jobs[0].location == "Aarhus"
    assert jobs[0].posted_date == "Deadline 2026-05-30"


def test_calibration_writes_expected_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_static(url: str, timeout_seconds: int):
        return CALIBRATION_HTML, url, []

    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_static_html", fake_static)

    result = capture_recipe_calibration("https://example.com/jobs", root=tmp_path, max_candidates=5)

    assert result.capture_mode == "static_html"
    assert result.candidate_count > 0
    for filename in ["page.html", "visible-text.txt", "candidate-elements.html", "selector-report.json", "summary.md"]:
        assert (result.artifact_dir / filename).exists()


def test_calibration_uses_recipe_rendered_mode_without_playwright_in_unit_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        "source_name: Rendered\n"
        "mode: rendered_html\n"
        "listing:\n"
        "  card_selector: .job-card\n"
        "  title_selector: h2\n"
        "  link_selector: a\n",
        encoding="utf-8",
    )
    calls = {}

    def fake_rendered(url: str, timeout_seconds: int):
        calls["rendered"] = url
        return CALIBRATION_HTML, url, []

    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_rendered_html", fake_rendered)

    result = capture_recipe_calibration("https://example.com/jobs", recipe_path=str(recipe_path), root=tmp_path)

    assert calls == {"rendered": "https://example.com/jobs"}
    assert result.capture_mode == "rendered_html"
