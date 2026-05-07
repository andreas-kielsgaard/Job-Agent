from __future__ import annotations

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
