from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.services import job_board_check_service
from job_agent.services.extraction_quality import CandidateQuality
from job_agent.services.job_board_check_service import (
    ExtractionQuality,
    check_job_board_compatibility,
    validate_public_url,
)


class FakeResponse:
    status_code = 200
    url = "https://example.com/jobs"
    text = """
    <main>
      <article>
        <a href="/jobs/sap-abap">SAP ABAP Consultant contract role</a>
        <p>Work on ABAP RAP, CDS, OData, Gateway and integration delivery for a long running SAP programme.</p>
      </article>
      <article>
        <a href="/jobs/apply">Apply now</a>
      </article>
    </main>
    """

    def raise_for_status(self) -> None:
        return None


def test_checker_reports_normal_html_quality(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse())

    report = check_job_board_compatibility("https://example.com/jobs", render=False)

    assert report.normal_html.candidate_count == 2
    assert report.normal_html.useful_title_count == 1
    assert report.normal_html.generic_title_count == 1
    assert report.normal_html.unique_url_count == 2
    assert report.normal_html.candidates[0].description_length >= 80
    assert report.recommendation == "current generic extractor is enough"


def test_checker_prefers_recipe_when_rendered_page_is_better(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyResponse(FakeResponse):
        text = "<html><body>No links before JavaScript</body></html>"

    rendered = ExtractionQuality(label="Playwright-rendered page")
    rendered.candidates = [
        CandidateQuality(
            title="SAP RAP Consultant contract",
            url="https://example.com/jobs/rendered",
            title_quality="useful",
            description_length=140,
            missing_fields=[],
        )
    ]
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: EmptyResponse())
    monkeypatch.setattr(job_board_check_service, "_extract_from_playwright", lambda *args, **kwargs: rendered)

    report = check_job_board_compatibility("https://example.com/jobs", render=True)

    assert report.normal_html.candidate_count == 0
    assert report.rendered_page is rendered
    assert report.recommendation == "later extraction recipe may be useful"


def test_validate_public_url_rejects_localhost() -> None:
    with pytest.raises(ValueError, match="Only public"):
        validate_public_url("http://localhost:8765/jobs")


def test_checker_can_validate_recipe_against_local_listing_and_detail_sample(tmp_path: Path) -> None:
    recipe_path = tmp_path / "recipe.yaml"
    listing_path = tmp_path / "listing.html"
    detail_path = tmp_path / "detail.html"
    recipe_path.write_text(
        """
source_name: Whitehall Shape
listing:
  card_selector: .job-item
  title_selector: h3 a
  link_selector: h3 a
  location_selector: .job-location
  workload_selector: .job-type
detail:
  follow: true
  use_json_ld: true
  max_detail_pages: 5
  request_delay_seconds: 1.0
pagination:
  page_link_selector: a.page-numbers
  next_selector: a.next.page-numbers
  max_pages: 4
accept:
  url_contains:
    - /job/
""",
        encoding="utf-8",
    )
    listing_path.write_text(
        """
<div class="job-item">
  <div class="job-type">Contract</div>
  <h3><a href="https://example.com/job/sap-eam/">SAP EAM Consultant</a></h3>
  <div class="job-location">Sweden</div>
</div>
<a class="page-numbers" href="https://example.com/sap-jobs/page/2/">2</a>
<a class="next page-numbers" href="https://example.com/sap-jobs/page/2/">Next</a>
""",
        encoding="utf-8",
    )
    detail_path.write_text(
        """
<link rel="canonical" href="https://example.com/job/sap-eam/">
<script type="application/ld+json">
{"@context":"https://schema.org/","@type":"JobPosting","title":"SAP EAM Consultant","description":"<p>Full SAP EAM posting with migration, validation, mapping, S/4HANA PM and integration details.</p>","datePosted":"2026-05-13","employmentType":"Contract","jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressCountry":"Sweden"}}}
</script>
""",
        encoding="utf-8",
    )

    report = check_job_board_compatibility(
        str(listing_path),
        render=False,
        recipe_path=recipe_path,
        base_url="https://example.com/sap-jobs/",
        detail_input_value=detail_path,
    )

    assert report.input_type == "local HTML"
    assert report.recommendation == "selected recipe looks compatible"
    findings = {finding.label: finding.status for finding in report.findings}
    assert findings["Listing cards"] == "pass"
    assert findings["Job URLs"] == "pass"
    assert findings["Detail navigation"] == "pass"
    assert findings["Pagination detection"] == "pass"
    assert findings["Detail sample fields"] == "pass"
    assert findings["Field: Title"] == "pass"
    assert findings["Field: Location"] == "pass"
    assert report.recipe_preview.detail_sample.posted_date == "2026-05-13"
