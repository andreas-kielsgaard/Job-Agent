from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.services.extraction_quality import ExtractionQuality, candidate_quality
from job_agent.services.job_board_recipe_service import (
    AcceptRecipe,
    DetailRecipe,
    JobBoardRecipe,
    ListingRecipe,
    PaginationLink,
    PaginationRecipe,
    PatternsRecipe,
    RejectRecipe,
    check_recipe_against_html,
    enrich_jobs_with_detail_pages,
    extract_job_detail_from_html,
    extract_jobs_with_recipe,
    extract_jobs_with_recipe_from_html,
    extract_jobs_with_recipe_from_url,
    find_pagination_links,
    load_job_board_recipe,
)
from job_agent.sources import extract_generic_jobs_from_html

FIXTURE_PATH = Path("tests/fixtures/synthetic-job-board.html")
RECIPE_PATH = Path("tests/fixtures/recipes/synthetic-job-board.yaml")
HTML = FIXTURE_PATH.read_text(encoding="utf-8")


def test_recipe_extracts_real_job_cards_and_dedupes_urls() -> None:
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", _recipe())

    assert [job.title for job in jobs] == ["SAP ABAP Consultant", "SAP Basis Consultant"]
    assert jobs[0].url == "https://example.com/jobs/sap-abap"
    assert jobs[0].source_confidence == "recipe"
    assert jobs[0].freshness_confidence == "recipe"
    assert jobs[0].extraction_notes == ["Recipe-based extraction; verify details manually."]


def test_recipe_extraction_result_explains_listing_count_mismatch() -> None:
    html = """
    <article class="job-card"><h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2></article>
    <article class="job-card"><h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant duplicate</a></h2></article>
    <article class="job-card"><h2><a class="job-link" href="/about">SAP Careers Overview</a></h2></article>
    <article class="job-card"><h2>SAP Missing Link Consultant</h2></article>
    """

    result = extract_jobs_with_recipe_from_html(html, "https://example.com", _recipe())

    assert result.listing_observed_count == 4
    assert result.listing_extracted_count == 1
    assert result.listing_duplicate_count == 1
    assert result.listing_rejected_count == 1
    assert result.listing_missing_url_count == 1


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


def test_detail_follow_false_does_not_fetch_detail_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", _recipe())
    recipe = _recipe(detail=DetailRecipe(follow=False, description_selector=".detail-description"))
    monkeypatch.setattr(
        "job_agent.services.job_board_recipe_service.requests.get", lambda *args, **kwargs: calls.append(args)
    )

    warnings = enrich_jobs_with_detail_pages(jobs, recipe)

    assert warnings == []
    assert calls == []


def test_detail_follow_true_fetches_and_enriches_candidate_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    detail_pages = {
        "https://example.com/jobs/sap-abap": """
        <main><h1>SAP ABAP Lead Consultant</h1><section class="detail-description">Full ABAP RAP contract description with delivery details and integration scope.</section><span class="detail-location">Aarhus</span><span class="detail-rate">DKK 1000/hour</span><time class="detail-posted">2026-05-08</time></main>
        """,
        "https://example.com/jobs/sap-basis": """
        <main><h1>SAP Basis Consultant</h1><section class="detail-description">Full Basis contract description with landscape operations and upgrade work.</section></main>
        """,
    }

    class FakeResponse:
        def __init__(self, url: str) -> None:
            self.text = detail_pages[url]
            self.url = url

        def raise_for_status(self) -> None:
            return None

    calls = []

    def fake_get(url: str, *args, **kwargs):
        calls.append(url)
        return FakeResponse(url)

    recipe = _recipe(
        detail=DetailRecipe(
            follow=True,
            title_selector="h1",
            description_selector=".detail-description",
            location_selector=".detail-location",
            rate_selector=".detail-rate",
            posted_date_selector=".detail-posted",
            max_detail_pages=5,
        )
    )
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", recipe)
    monkeypatch.setattr("job_agent.services.job_board_recipe_service.requests.get", fake_get)

    warnings = enrich_jobs_with_detail_pages(jobs, recipe)

    assert warnings == []
    assert calls == ["https://example.com/jobs/sap-abap", "https://example.com/jobs/sap-basis"]
    assert jobs[0].title == "SAP ABAP Lead Consultant"
    assert jobs[0].description.startswith("Full ABAP RAP contract")
    assert jobs[0].location == "Copenhagen"
    assert jobs[0].rate == "DKK 900/hour"
    assert jobs[0].posted_date == "2026-05-07"
    assert "Detail page fetched by recipe; verify details manually." in jobs[0].extraction_notes


def test_detail_max_detail_pages_limits_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FakeResponse:
        text = "<main><section class='detail-description'>Full detail page text.</section></main>"

        def raise_for_status(self) -> None:
            return None

    recipe = _recipe(detail=DetailRecipe(follow=True, description_selector=".detail-description", max_detail_pages=1))
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", recipe)
    monkeypatch.setattr(
        "job_agent.services.job_board_recipe_service.requests.get",
        lambda url, *args, **kwargs: calls.append(url) or FakeResponse(),
    )

    warnings = enrich_jobs_with_detail_pages(jobs, recipe)

    assert warnings == []
    assert calls == ["https://example.com/jobs/sap-abap"]


def test_source_run_detail_policy_can_enrich_all_retained_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    detail_pages = {
        "https://example.com/jobs/sap-abap": "<main><section class='detail-description'>ABAP detail text with enough useful context for a report.</section></main>",
        "https://example.com/jobs/sap-basis": "<main><section class='detail-description'>Basis detail text with enough useful context for a report.</section></main>",
    }

    class FakeResponse:
        def __init__(self, text: str, url: str) -> None:
            self.text = text
            self.url = url

        def raise_for_status(self) -> None:
            return None

    def fake_listing_fetch(url: str, timeout_seconds: int):
        return HTML, "https://example.com/jobs", []

    calls = []

    def fake_detail_fetch(url: str, *args, **kwargs):
        calls.append(url)
        return FakeResponse(detail_pages[url], url)

    recipe = _recipe(detail=DetailRecipe(follow=True, description_selector=".detail-description", max_detail_pages=1))
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_listing_fetch)
    monkeypatch.setattr("job_agent.services.job_board_recipe_service.requests.get", fake_detail_fetch)

    result = extract_jobs_with_recipe_from_url(
        "https://example.com/jobs",
        recipe,
        use_recipe_detail_limit=False,
        detail_page_limit=None,
    )

    assert calls == ["https://example.com/jobs/sap-abap", "https://example.com/jobs/sap-basis"]
    assert result.detail_fetch_count == 2
    assert result.detail_enriched_count == 2
    assert all(job.description.startswith(("ABAP detail", "Basis detail")) for job in result.jobs)


def test_failed_detail_fetch_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeError(Exception):
        pass

    def fail(*args, **kwargs):
        import requests

        raise requests.RequestException("timeout")

    recipe = _recipe(detail=DetailRecipe(follow=True, description_selector=".detail-description"))
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", recipe)
    monkeypatch.setattr("job_agent.services.job_board_recipe_service.requests.get", fail)

    warnings = enrich_jobs_with_detail_pages(jobs, recipe)

    assert len(warnings) == 2
    assert all("Detail fetch failed" in warning for warning in warnings)


def test_detail_follow_true_without_selectors_returns_warning() -> None:
    recipe = _recipe(detail=DetailRecipe(follow=True))
    jobs = extract_jobs_with_recipe(HTML, "https://example.com", recipe)

    warnings = enrich_jobs_with_detail_pages(jobs, recipe)

    assert warnings == ["detail.follow is true, but no detail selectors are configured."]


def test_detail_json_ld_extracts_full_posting_fields_from_saved_page_shape() -> None:
    html = """
    <html><head><link rel="canonical" href="https://example.com/job/sap-eam/"></head>
    <body>
      <main class="job-single"><h1>SAP EAM / PM Migration Consultant</h1></main>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": "SAP EAM / PM Migration Consultant",
        "description": "<p>SAP EAM migration role
        with validation, mapping, blueprinting, S/4HANA PM, Fiori and integration responsibilities.</p>",
        "datePosted": "2026-05-13",
        "employmentType": "Contract",
        "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressCountry": "Sweden"}}
      }
      </script>
    </body></html>
    """
    recipe = _recipe(detail=DetailRecipe(follow=True, use_json_ld=True))

    job = extract_job_detail_from_html(html, "https://example.com/jobs", recipe)

    assert job.title == "SAP EAM / PM Migration Consultant"
    assert job.url == "https://example.com/job/sap-eam/"
    assert job.location == "Sweden"
    assert job.workload == "Contract"
    assert job.posted_date == "2026-05-13"
    assert "S/4HANA PM" in job.description


def test_detail_patterns_extract_whitehall_detail_text_fields() -> None:
    html = """
    <html><head><link rel="canonical" href="https://www.whitehallresources.com/job/sap-mobile-developer-31782/"></head>
    <body>
      <div class="job-single">
        <div class="left-col">
          <div class="job-type">Contract</div>
          <h1>SAP Mobile Developer</h1>
          <div class="job-details"><div class="job-location">Madrid</div></div>
          <p>
            Additional Information * Location: Remote * Start: ASAP *
            Duration: Until December 2026 * Languages: English (B2+ required) * Rate:
          </p>
        </div>
      </div>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": "SAP Mobile Developer",
        "description": "<p>SAP Mobile Developer MDK implementation role.</p>",
        "datePosted": "2026-05-01",
        "employmentType": "CONTRACTOR",
        "jobLocation": {
          "@type": "Place",
          "address": {"@type": "PostalAddress", "addressCountry": "Spain"}
        }
      }
      </script>
    </body></html>
    """
    recipe = _recipe(
        detail=DetailRecipe(
            follow=True,
            use_json_ld=True,
            title_selector=".job-single h1",
            location_selector=".job-single .job-location",
            workload_selector=".job-single .job-type",
        ),
        patterns=PatternsRecipe(
            remote_regex=r"\b(?P<remote>Remote|Hybrid|Office based)\b",
            start_date_regex=r"Start:\s*(?P<start_date>[^*\n\r]+?)(?=\s+\*|\s+Duration:|$)",
            language_regex=r"(?:Languages?:\s*|Fluent in\s+)(?P<language>English(?:\s*\([^)]*\))?)",
        ),
    )

    job = extract_job_detail_from_html(html, "https://www.whitehallresources.com/sap-jobs/", recipe)

    assert job.title == "SAP Mobile Developer"
    assert job.location == "Madrid"
    assert job.remote == "Remote"
    assert job.workload == "Contract"
    assert job.posted_date == "2026-05-01"
    assert job.start_date == "ASAP"
    assert job.languages == ["English (B2+ required)"]


def test_recipe_detects_pagination_links_without_fetching_pages() -> None:
    html = """
    <nav class="pagination">
      <span class="page-numbers current">1</span>
      <a class="page-numbers" href="/sap-jobs/page/2/">2</a>
      <a class="page-numbers" href="/sap-jobs/page/3/">3</a>
      <a class="next page-numbers" href="/sap-jobs/page/2/">Next &raquo;</a>
    </nav>
    """
    recipe = _recipe(
        pagination=PaginationRecipe(
            page_link_selector="a.page-numbers",
            next_selector="a.next.page-numbers",
            max_pages=3,
        )
    )

    links = find_pagination_links(html, "https://example.com/sap-jobs/", recipe)

    assert [link.url for link in links] == [
        "https://example.com/sap-jobs/page/2/",
        "https://example.com/sap-jobs/page/3/",
    ]
    assert links[0].is_next is True
    assert links[1].is_next is False


def test_recipe_can_proof_fetch_one_pagination_page(monkeypatch: pytest.MonkeyPatch) -> None:
    page_1 = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2><p class="summary">ABAP listing one with useful context.</p></div>
    <a class="page-numbers" href="https://example.com/jobs/page/2/">2</a>
    <a class="next page-numbers" href="https://example.com/jobs/page/2/">Next</a>
    """
    page_2 = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-basis">SAP Basis Consultant</a></h2><p class="summary">Basis listing two with useful context.</p></div>
    """
    calls = []

    def fake_fetch(url: str, timeout_seconds: int):
        calls.append(url)
        if url.endswith("/page/2/"):
            return page_2, url, []
        return page_1, "https://example.com/jobs", []

    recipe = _recipe(
        pagination=PaginationRecipe(
            page_link_selector="a.page-numbers",
            next_selector="a.next.page-numbers",
            max_pages=4,
            request_delay_seconds=0,
        )
    )
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch)

    result = extract_jobs_with_recipe_from_url(
        "https://example.com/jobs",
        recipe,
        fetch_pagination=True,
        pagination_page_limit=2,
    )

    assert calls == ["https://example.com/jobs", "https://example.com/jobs/page/2/"]
    assert [job.title for job in result.jobs] == ["SAP ABAP Consultant", "SAP Basis Consultant"]
    assert result.pagination_fetch_count == 1


def test_pagination_detection_reads_embedded_json_html_fragments() -> None:
    html = """
    <script type="application/json">
    {"initialPagination":"<div class='paginator'><a href='/projects?pagenr=2#list'>2</a><a class='next' href='/projects?pagenr=2#list'>next</a></div>"}
    </script>
    """
    recipe = _recipe(
        pagination=PaginationRecipe(
            page_link_selector=".paginator a",
            next_selector=".paginator a.next",
            max_pages=18,
        )
    )

    links = find_pagination_links(html, "https://www.freelancermap.com/projects", recipe)

    assert links == [
        PaginationLink(
            label="2",
            url="https://www.freelancermap.com/projects?pagenr=2#list",
            is_next=True,
        )
    ]


def test_pagination_detection_dedupes_link_rel_next_against_embedded_page_links() -> None:
    html = """
    <link rel="next" href="https://www.freelancermap.com/projects?pagenr=2">
    <script type="application/json">
    {"initialPagination":"<div class='paginator'><a href='/projects?pagenr=2#list'>2</a><a href='/projects?pagenr=3#list'>3</a><a href='/projects?pagenr=4#list'>4</a></div>"}
    </script>
    """
    recipe = _recipe(
        pagination=PaginationRecipe(
            page_link_selector='a[href*="pagenr="]',
            next_selector='link[rel="next"]',
            max_pages=4,
        )
    )

    links = find_pagination_links(html, "https://www.freelancermap.com/projects", recipe)

    assert [(link.label, link.url, link.is_next) for link in links] == [
        ("2", "https://www.freelancermap.com/projects?pagenr=2#list", True),
        ("3", "https://www.freelancermap.com/projects?pagenr=3#list", False),
        ("4", "https://www.freelancermap.com/projects?pagenr=4#list", False),
    ]


def test_detail_extract_scopes_popup_html_to_modal_before_listing_heading() -> None:
    html = """
    <main>
      <h1>Find the perfect project</h1>
      <div class="modal">
        <h1>SAP Data Migration Consultant</h1>
        <div class="project-header-info-list"><span>100% remote</span><span>Freelance</span><span>Start date 9 / 2026</span></div>
        <div class="badge-content-city">lisboa, Portugal</div>
        <section class="project-body">Description We are looking for an SAP Data Migration Consultant.</section>
      </div>
    </main>
    """
    recipe = _recipe(
        detail=DetailRecipe(
            follow=True,
            title_selector=[".project-show-single-page h1", "main h1", "h1"],
            description_selector=".project-body",
            location_selector=".badge-content-city",
            remote_selector=".project-header-info-list > span:nth-of-type(1)",
            workload_selector=".project-header-info-list > span:nth-of-type(2)",
            start_date_selector=".project-header-info-list > span:nth-of-type(3)",
        )
    )

    job = extract_job_detail_from_html(html, "https://www.freelancermap.com/projects", recipe)

    assert job.title == "SAP Data Migration Consultant"
    assert job.location == "lisboa, Portugal"
    assert job.remote == "100% remote"
    assert job.workload == "Freelance"
    assert job.start_date == "Start date 9 / 2026"


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
    recipe = load_job_board_recipe(RECIPE_PATH)

    assert recipe.source_name == "Synthetic Example Job Board"
    assert recipe.listing.card_selector == ".job-card"
    assert recipe.mode == "static_html"
    assert recipe.listing.title_selector == [".job-heading", ".job-title", "h2"]


def test_cli_recipe_command_runs_against_local_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    from job_agent.cli import test_recipe

    test_recipe(
        str(RECIPE_PATH),
        str(FIXTURE_PATH),
        base_url="https://example.com/jobs",
    )

    output = capsys.readouterr().out
    assert "Jobs extracted: 2" in output
    assert "SAP ABAP Consultant" in output
    assert "https://example.com/jobs/sap-abap" in output
    assert "Location: Copenhagen" in output
    assert "Remote/work arrangement: Not listed" in output
    assert "Rate/pay: DKK 900/hour" in output
    assert "Workload/work type: Not listed" in output
    assert "Posted date: 2026-05-07" in output
    assert "Description: ABAP RAP CDS OData Gateway integration contract with hands-on delivery scope." in output


def test_cli_recipe_command_can_save_source_health(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from job_agent.cli import test_recipe

    saved = {}

    class FakeHealthService:
        def save_preview(self, source_id, preview):
            saved["source_id"] = source_id
            saved["count"] = preview.extracted_job_count

    monkeypatch.setattr("job_agent.services.source_health_service.SourceHealthService", lambda: FakeHealthService())

    test_recipe(
        str(RECIPE_PATH),
        str(FIXTURE_PATH),
        base_url="https://example.com/jobs",
        source_id="sample-jobs",
    )

    output = capsys.readouterr().out
    assert saved == {"source_id": "sample-jobs", "count": 2}
    assert "Source health saved: sample-jobs" in output


def test_url_extraction_uses_recipe_rendered_mode_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {}

    def fake_rendered(url: str, timeout_seconds: int):
        calls["rendered"] = url
        return HTML, "https://example.com/jobs", []

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_rendered_html", fake_rendered)

    result = extract_jobs_with_recipe_from_url("https://example.com/jobs", _recipe(mode="rendered_html"))

    assert calls == {"rendered": "https://example.com/jobs"}
    assert result.mode_used == "rendered_html"
    assert len(result.jobs) == 2


def test_cli_local_fixture_ignores_rendered_recipe_mode(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from job_agent.cli import test_recipe

    test_recipe(
        str(RECIPE_PATH),
        str(FIXTURE_PATH),
        base_url="https://example.com/jobs",
    )
    normal_output = capsys.readouterr().out
    assert "Input mode: local_fixture_html" in normal_output

    rendered_recipe = tmp_path / "rendered-mode-recipe.yaml"
    rendered_recipe.write_text(
        RECIPE_PATH.read_text(encoding="utf-8").replace("mode: static_html", "mode: rendered_html"),
        encoding="utf-8",
    )
    test_recipe(str(rendered_recipe), str(FIXTURE_PATH), base_url="https://example.com/jobs")
    output = capsys.readouterr().out

    assert "Input mode: local_fixture_html" in output
    assert "Warning: Local fixture HTML ignores recipe mode: rendered_html." in output


def _recipe(
    card_selector: str = ".job-card",
    detail: DetailRecipe | None = None,
    mode: str = "static_html",
    pagination: PaginationRecipe | None = None,
    patterns: PatternsRecipe | None = None,
) -> JobBoardRecipe:
    return JobBoardRecipe(
        source_name="Test Board",
        mode=mode,
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
        detail=detail or DetailRecipe(),
        pagination=pagination or PaginationRecipe(),
        patterns=patterns or PatternsRecipe(),
    )
