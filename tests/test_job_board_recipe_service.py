from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_agent.models import Job
from job_agent.services.extraction_quality import ExtractionQuality, candidate_quality
from job_agent.services.job_board_recipe_service import (
    AcceptRecipe,
    AccessRecipe,
    ApiFieldMapping,
    ApiPaginationRecipe,
    ApiRequestRecipe,
    DetailRecipe,
    JobBoardRecipe,
    ListingRecipe,
    PaginationLink,
    PaginationRecipe,
    PatternsRecipe,
    RejectRecipe,
    _pagination_urls_to_fetch,
    check_recipe_against_html,
    discover_visible_total_job_count,
    enrich_jobs_with_detail_pages,
    extract_job_detail_from_html,
    extract_jobs_with_recipe,
    extract_jobs_with_recipe_from_api_payload,
    extract_jobs_with_recipe_from_html,
    extract_jobs_with_recipe_from_url,
    find_pagination_links,
    job_board_recipe_from_mapping,
    load_job_board_recipe,
    load_project_job_board_recipe,
)
from job_agent.services.recipes.discovery import discover_pagination_links
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


def test_generated_class_card_selector_falls_back_to_accepted_job_links() -> None:
    html = """
    <!doctype html>
    <html><body>
      <main>
        <div class="results">
          <div class="sc-AykKG sc-fzXfQZ xNuff">
            <a class="sc-AykKE newHash" href="/job/a1/sap-btp-consultant">
              <span>SAP BTP Consultant</span>
              <span>Germany, Hamburg</span>
              <span>Contract role with SAP BTP, ABAP and S/4HANA delivery.</span>
            </a>
          </div>
          <div class="sc-AykKG sc-fzXfQZ xNuff">
            <a class="sc-AykKE newHash" href="/job/a2/sap-fi-consultant">
              <span>SAP FI Consultant</span>
              <span>Germany, Berlin</span>
              <span>Freelance SAP FI role with S/4HANA migration scope.</span>
            </a>
          </div>
        </div>
      </main>
    </body></html>
    """
    recipe = job_board_recipe_from_mapping(
        {
            "source_name": "Styled Board",
            "listing": {
                "card_selector": "div.sc-AykKG.sc-LzLvc",
                "title_selector": 'a.sc-AykKE.fwNdOp[href*="/job/"]',
                "link_selector": 'a.sc-AykKE.fwNdOp[href*="/job/"]',
            },
            "accept": {"url_contains": ["/job/"]},
            "limits": {"max_cards": 10},
        }
    )

    result = extract_jobs_with_recipe_from_html(html, "https://example.com/jobs", recipe)

    assert [job.title for job in result.jobs] == ["SAP BTP Consultant", "SAP FI Consultant"]
    assert [job.url for job in result.jobs] == [
        "https://example.com/job/a1/sap-btp-consultant",
        "https://example.com/job/a2/sap-fi-consultant",
    ]
    assert result.listing_observed_count == 2


def test_api_recipe_schema_accepts_listing_api_without_html_selectors() -> None:
    recipe = job_board_recipe_from_mapping(
        {
            "source_name": "API Board",
            "start_url": "https://example.com/jobs",
            "listing_api": {
                "method": "POST",
                "url": "https://example.com/api/search",
                "body": {"query": "SAP", "resultFrom": 0},
                "results_path": "result.results",
                "total_path": "result.hits",
                "fields": {
                    "title": "title",
                    "url_template": "https://example.com/jobs/{slug}/{jobReference}/",
                    "location": "location",
                    "description_html": "description",
                },
                "pagination": {
                    "strategy": "offset",
                    "offset_param": "resultFrom",
                    "page_size_param": "resultSize",
                    "page_size": 20,
                    "max_pages": 3,
                },
            },
        }
    )

    assert recipe.listing.card_selector == ""
    assert recipe.listing_api.method == "POST"
    assert recipe.listing_api.results_path == "result.results"


def test_api_recipe_schema_rejects_credentials_and_missing_access_plan() -> None:
    with pytest.raises(ValueError, match="missing required listing selector"):
        job_board_recipe_from_mapping({"source_name": "Broken"})

    with pytest.raises(ValueError, match="must not contain credentials"):
        job_board_recipe_from_mapping(
            {
                "source_name": "API Board",
                "listing_api": {
                    "url": "https://example.com/api/search",
                    "headers": {"Authorization": "Bearer secret"},
                    "results_path": "items",
                    "fields": {"title": "title", "url": "url"},
                },
            }
        )

    with pytest.raises(ValueError, match="listing_api.body must not contain credentials"):
        job_board_recipe_from_mapping(
            {
                "source_name": "API Board",
                "listing_api": {
                    "url": "https://example.com/api/search",
                    "body": {"token": "secret"},
                    "results_path": "items",
                    "fields": {"title": "title", "url": "url"},
                },
            }
        )


def test_extract_jobs_with_api_payload_maps_json_records_to_jobs() -> None:
    payload = {
        "result": {
            "hits": 95,
            "results": [
                {
                    "title": "SAP ABAP Consultant",
                    "slug": "sap-abap-consultant",
                    "jobReference": "123",
                    "location": "Remote",
                    "remoteWorkingAvailable": True,
                    "salaryText": "EUR 750/day",
                    "jobType": "Contract",
                    "postDate": "2026-06-09",
                    "description": "<p>ABAP RAP project with CDS and OData delivery.</p>",
                }
            ],
        }
    }

    result = extract_jobs_with_recipe_from_api_payload(payload, "https://example.com/en-gb/job-search/", _api_recipe())

    assert result.access_strategy == "api"
    assert result.visible_total_job_count == 95
    assert result.records_observed_count == 1
    assert result.jobs[0].title == "SAP ABAP Consultant"
    assert result.jobs[0].url == "https://example.com/en-gb/job/sap-abap-consultant/123/"
    assert result.jobs[0].remote == "Yes"
    assert "ABAP RAP project" in result.jobs[0].description
    checks = {check.capability: check for check in result.capability_checks}
    assert checks["api_listing"].status == "pass"


def test_api_pagination_fetches_bounded_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    pages = [
        {
            "result": {
                "hits": 3,
                "results": [
                    {"title": "SAP Consultant 1", "slug": "sap-1", "jobReference": "1", "description": "SAP one"}
                ],
            }
        },
        {
            "result": {
                "hits": 3,
                "results": [
                    {"title": "SAP Consultant 2", "slug": "sap-2", "jobReference": "2", "description": "SAP two"}
                ],
            }
        },
        {
            "result": {
                "hits": 3,
                "results": [
                    {"title": "SAP Consultant 3", "slug": "sap-3", "jobReference": "3", "description": "SAP three"}
                ],
            }
        },
    ]

    def fake_fetch_json_api(**kwargs):
        calls.append(kwargs)
        index = len(calls) - 1
        return pages[index], f"https://example.com/api/search?call={index}", []

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_json_api", fake_fetch_json_api)

    result = extract_jobs_with_recipe_from_url(
        "https://example.com/en-gb/job-search/",
        _api_recipe(),
        fetch_pagination=True,
        pagination_page_limit=3,
    )

    assert [job.title for job in result.jobs] == ["SAP Consultant 1", "SAP Consultant 2", "SAP Consultant 3"]
    assert result.api_request_count == 3
    assert result.pagination_fetch_count == 2
    assert calls[1]["body"]["resultFrom"] == 20
    assert calls[2]["body"]["resultFrom"] == 40
    checks = {check.capability: check for check in result.capability_checks}
    assert checks["api_pagination"].status == "pass"


def test_api_listing_can_merge_html_detail_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "result": {
            "hits": 1,
            "results": [
                {
                    "title": "SAP Basis Consultant",
                    "slug": "sap-basis",
                    "jobReference": "9",
                    "description": "Short listing text.",
                }
            ],
        }
    }
    detail_html = (
        "<main><h1>SAP Basis Lead Consultant</h1>"
        "<section class='detail-description'>Basis migration and S/4HANA operations contract.</section>"
        "<span class='detail-location'>Copenhagen</span></main>"
    )

    class FakeResponse:
        text = detail_html
        url = "https://example.com/en-gb/job/sap-basis/9/"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "job_agent.services.job_board_recipe_service._fetch_json_api",
        lambda **kwargs: (payload, "https://example.com/api/search", []),
    )
    monkeypatch.setattr(
        "job_agent.services.job_board_recipe_service._requests_get_with_session_state",
        lambda *args, **kwargs: FakeResponse(),
    )
    recipe = _api_recipe(
        detail=DetailRecipe(
            follow=True,
            title_selector="h1",
            description_selector=".detail-description",
            location_selector=".detail-location",
            max_detail_pages=1,
        )
    )

    result = extract_jobs_with_recipe_from_url(
        "https://example.com/en-gb/job-search/",
        recipe,
        detail_page_limit=1,
    )

    assert result.detail_fetch_count == 1
    assert result.detail_enriched_count == 1
    assert result.jobs[0].title == "SAP Basis Lead Consultant"
    assert result.jobs[0].location == "Copenhagen"
    assert "Basis migration" in result.jobs[0].description


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


def test_visible_total_count_failure_when_extractor_reaches_too_few_jobs() -> None:
    cards = "\n".join(
        (
            "<article class='job-card'>"
            f"<h2><a class='job-link' href='/jobs/job-{index}'>SAP Consultant {index}</a></h2>"
            "<p class='summary'>SAP contract role with delivery context.</p>"
            "</article>"
        )
        for index in range(1, 24)
    )
    html = f"<main><p>66 projects found</p>{cards}</main>"

    result = extract_jobs_with_recipe_from_html(html, "https://example.com", _recipe())

    checks = {check.capability: check for check in result.capability_checks}
    assert result.visible_total_job_count == 66
    assert len(result.jobs) == 23
    assert checks["listing_total_access"].status == "fail"
    assert "advertise 66 posting" in checks["listing_total_access"].detail


def test_visible_total_count_allows_bounded_pagination_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    page_1 = """
    <main>
      <p>Showing 1-10 of 30 jobs</p>
      <article class="job-card"><h2><a class="job-link" href="/jobs/sap-1">SAP Consultant 1</a></h2><p class="summary">SAP role one with useful context.</p></article>
      <a class="page-link" href="/jobs?page=2">2</a>
      <a class="page-link" href="/jobs?page=3">3</a>
    </main>
    """
    page_2 = """
    <main>
      <article class="job-card"><h2><a class="job-link" href="/jobs/sap-2">SAP Consultant 2</a></h2><p class="summary">SAP role two with useful context.</p></article>
    </main>
    """

    def fake_fetch(url: str, timeout_seconds: int):
        if "page=2" in url:
            return page_2, url, []
        return page_1, "https://example.com/jobs?page=1", []

    recipe = _recipe(
        pagination=PaginationRecipe(
            page_link_selector='a.page-link[href*="page="]',
            max_pages=3,
            request_delay_seconds=0,
        )
    )
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch)

    result = extract_jobs_with_recipe_from_url(
        "https://example.com/jobs?page=1",
        recipe,
        fetch_pagination=True,
        pagination_page_limit=2,
    )

    checks = {check.capability: check for check in result.capability_checks}
    assert [job.title for job in result.jobs] == ["SAP Consultant 1", "SAP Consultant 2"]
    assert checks["listing_total_access"].status == "pass"
    assert "confirmed pagination" in checks["listing_total_access"].detail


def test_url_pagination_can_follow_listing_expansion_links(monkeypatch: pytest.MonkeyPatch) -> None:
    page_1 = """
    <main>
      <p>Jobs 4</p>
      <ul>
        <li class="feature">
          <a href="/remote-jobs/acme-sap-manager">SAP Manager</a>
          <span class="company">Acme</span>
        </li>
        <li class="view-all"><a href="/categories/remote-sap-jobs">View all 3 SAP jobs</a></li>
      </ul>
    </main>
    """
    category_page = """
    <main>
      <ul>
        <li class="feature">
          <a href="/remote-jobs/acme-sap-manager">SAP Manager</a>
          <span class="company">Acme</span>
        </li>
        <li class="feature">
          <a href="/remote-jobs/contoso-sap-lead">SAP Lead Consultant</a>
          <span class="company">Contoso</span>
        </li>
        <li class="feature">
          <a href="/remote-jobs/globex-sap-architect">SAP Architect</a>
          <span class="company">Globex</span>
        </li>
        <li class="feature">
          <a href="/remote-jobs/initech-sap-analyst">SAP Analyst</a>
          <span class="company">Initech</span>
        </li>
      </ul>
    </main>
    """

    def fake_fetch(url: str, timeout_seconds: int):
        if "/categories/" in url:
            return category_page, "https://weworkremotely.com/categories/remote-sap-jobs", []
        return page_1, "https://weworkremotely.com/remote-jobs/search", []

    recipe = JobBoardRecipe(
        source_name="WWR fixture",
        listing=ListingRecipe(
            card_selector="li.feature",
            title_selector='a[href*="/remote-jobs/"]',
            link_selector='a[href*="/remote-jobs/"]',
            company_selector=".company",
        ),
        accept=AcceptRecipe(url_contains=["/remote-jobs/"]),
        reject=RejectRecipe(),
        pagination=PaginationRecipe(
            page_link_selector='li.view-all a[href*="/categories/"]',
            max_pages=2,
            request_delay_seconds=0,
        ),
    )
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch)

    result = extract_jobs_with_recipe_from_url(
        "https://weworkremotely.com/remote-jobs/search",
        recipe,
        fetch_pagination=True,
        pagination_page_limit=2,
    )

    checks = {check.capability: check for check in result.capability_checks}
    assert [job.title for job in result.jobs] == [
        "SAP Manager",
        "SAP Lead Consultant",
        "SAP Architect",
        "SAP Analyst",
    ]
    assert result.pagination_fetch_attempts == ["https://weworkremotely.com/categories/remote-sap-jobs"]
    assert result.pagination_unique_jobs_from_fetched_pages == 3
    assert checks["listing_total_access"].status == "pass"
    assert checks["pagination_navigation"].status == "pass"


def test_url_pagination_can_follow_public_feed_links(monkeypatch: pytest.MonkeyPatch) -> None:
    page_1 = """
    <main>
      <p>Jobs 3</p>
      <a href="/categories/remote-product-jobs.rss">Product RSS</a>
      <a href="/categories/remote-engineering-jobs.rss">Engineering RSS</a>
    </main>
    """
    product_feed = """
    <rss><channel>
      <item>
        <title>SAP Product Manager</title>
        <guid>https://weworkremotely.com/remote-jobs/acme-sap-product-manager</guid>
        <region>Remote</region>
        <type>Contract</type>
        <pubDate>Fri, 12 Jun 2026 12:00:00 +0000</pubDate>
        <description>&lt;p&gt;SAP product delivery role with S/4HANA programme context and stakeholder work.&lt;/p&gt;</description>
      </item>
      <item>
        <title>SAP Delivery Lead</title>
        <guid>https://weworkremotely.com/remote-jobs/contoso-sap-delivery-lead</guid>
        <region>EMEA</region>
        <type>Contract</type>
        <description>&lt;p&gt;SAP delivery lead role with roadmap, integration and rollout ownership.&lt;/p&gt;</description>
      </item>
    </channel></rss>
    """
    engineering_feed = """
    <rss><channel>
      <item>
        <title>SAP Platform Engineer</title>
        <guid>https://weworkremotely.com/remote-jobs/globex-sap-platform-engineer</guid>
        <region>Anywhere</region>
        <type>Contract</type>
        <description>&lt;p&gt;SAP platform engineering role with BTP, integration and operations scope.&lt;/p&gt;</description>
      </item>
    </channel></rss>
    """

    def fake_fetch(url: str, timeout_seconds: int):
        if url.endswith("remote-product-jobs.rss"):
            return product_feed, "https://weworkremotely.com/categories/remote-product-jobs.rss", []
        if url.endswith("remote-engineering-jobs.rss"):
            return engineering_feed, "https://weworkremotely.com/categories/remote-engineering-jobs.rss", []
        return page_1, "https://weworkremotely.com/remote-jobs/search", []

    recipe = JobBoardRecipe(
        source_name="WWR feed fixture",
        listing=ListingRecipe(
            card_selector="item",
            title_selector="title",
            link_selector=["guid", "link"],
            location_selector=["region", "state", "country"],
            workload_selector="type",
            posted_date_selector=["pubDate", "pubdate"],
            description_selector="description",
        ),
        accept=AcceptRecipe(url_contains=["/remote-jobs/"]),
        reject=RejectRecipe(),
        pagination=PaginationRecipe(
            page_link_selector='a[href$=".rss"]',
            max_pages=3,
            request_delay_seconds=0,
        ),
    )
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch)

    result = extract_jobs_with_recipe_from_url(
        "https://weworkremotely.com/remote-jobs/search",
        recipe,
        fetch_pagination=True,
        pagination_page_limit=3,
    )

    checks = {check.capability: check for check in result.capability_checks}
    assert [job.title for job in result.jobs] == [
        "SAP Platform Engineer",
        "SAP Product Manager",
        "SAP Delivery Lead",
    ]
    assert result.jobs[0].url == "https://weworkremotely.com/remote-jobs/globex-sap-platform-engineer"
    assert result.jobs[0].description == "SAP platform engineering role with BTP, integration and operations scope."
    assert result.jobs[1].posted_date == "Fri, 12 Jun 2026 12:00:00 +0000"
    assert result.pagination_fetch_count == 2
    assert result.pagination_unique_jobs_from_fetched_pages == 3
    assert checks["listing_total_access"].status == "pass"
    assert checks["pagination_navigation"].status == "pass"


def test_visible_total_count_reads_formatted_showing_total() -> None:
    html = "<main><p>Showing 1-25 of 1,234 projects</p></main>"

    assert discover_visible_total_job_count(html) == 1234


def test_visible_total_count_reads_search_results_phrase() -> None:
    html = "<main><p>21 search results</p><p>For Permanent and Contract, SAP</p></main>"

    assert discover_visible_total_job_count(html) == 21


def test_visible_total_count_ignores_footer_phone_and_jobs_email() -> None:
    html = """
    <main>
      <p>21 search results</p>
      <article><a href="/job/1">SAP Consultant</a></article>
    </main>
    <footer>
      <p>London, EC3N 1DL United Kingdom</p>
      <p>+44 207 337 0814 jobs@washingtonfrank.com</p>
      <a href="/jobs-by-email">Jobs by email</a>
    </footer>
    """

    assert discover_visible_total_job_count(html) == 21


def test_visible_total_count_ignores_phone_when_no_result_count_present() -> None:
    html = """
    <main>
      <article><a href="/job/1">SAP Consultant</a></article>
    </main>
    <footer>
      <p>Contact +44 207 337 0814 jobs@washingtonfrank.com</p>
    </footer>
    """

    assert discover_visible_total_job_count(html) == 0


def test_pagination_urls_skip_first_page_links() -> None:
    links = [
        PaginationLink(label="1", url="https://example.com/jobs?pagenr=1"),
        PaginationLink(label="2", url="https://example.com/jobs?pagenr=2"),
        PaginationLink(label="3", url="https://example.com/jobs?pagenr=3"),
    ]

    assert _pagination_urls_to_fetch(links, max_pages=4) == [
        "https://example.com/jobs?pagenr=2",
        "https://example.com/jobs?pagenr=3",
    ]


def test_page_query_pagination_normalizes_undefined_links_to_current_search_url() -> None:
    html = """
    <nav class="search-pagination-wrap">
      <a class="page-link" href="undefined?page=1" aria-label="Page 1">1</a>
      <a class="page-link" href="undefined?page=2" aria-label="Go to page 2">2</a>
      <a class="page-link" href="undefined?page=3" aria-label="Go to page 3">3</a>
      <a class="page-link more" href="undefined?page=2" aria-label="Next">Next</a>
    </nav>
    """
    recipe = _recipe(
        pagination=PaginationRecipe(
            page_link_selector='a.page-link[href*="page="]',
            next_selector='a.page-link[href*="page="][aria-label*="Next"]',
            max_pages=3,
        )
    )

    links = find_pagination_links(
        html,
        "https://www.experis.pl/en/search?page=1&searchKeyword=SAP",
        recipe,
    )

    assert [(link.label, link.url, link.is_next) for link in links] == [
        ("1", "https://www.experis.pl/en/search?page=1&searchKeyword=SAP", False),
        ("2", "https://www.experis.pl/en/search?page=2&searchKeyword=SAP", True),
        ("3", "https://www.experis.pl/en/search?page=3&searchKeyword=SAP", False),
    ]
    assert _pagination_urls_to_fetch(links, max_pages=3) == [
        "https://www.experis.pl/en/search?page=2&searchKeyword=SAP",
        "https://www.experis.pl/en/search?page=3&searchKeyword=SAP",
    ]


def test_recipe_access_requirement_becomes_capability_failure() -> None:
    html = """
    <article class="job-card">
      <h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2>
      <p class="summary">ABAP listing one with useful context.</p>
    </article>
    """
    recipe = _recipe(
        access=AccessRecipe(
            requires_session=True,
            session_scope="example-projects",
            setup_hint="Sign in before verifying this source.",
        )
    )

    result = extract_jobs_with_recipe_from_html(html, "https://example.com", recipe)

    checks = {check.capability: check for check in result.capability_checks}
    assert [job.title for job in result.jobs] == ["SAP ABAP Consultant"]
    assert checks["source_access"].status == "fail"
    assert checks["source_access"].expected is True
    assert "requires a connected session" in checks["source_access"].detail
    assert "example-projects" in checks["source_access"].detail


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


def test_recipe_does_not_treat_bare_job_id_as_rate() -> None:
    html = """
    <article class="job-card">
      <h2><a class="job-link" href="/jobs/sap-abap-34770">SAP ABAP Consultant 34770</a></h2>
      <span class="rate">34770</span>
      <p class="summary">ABAP RAP CDS OData Gateway contract with delivery scope.</p>
    </article>
    """
    jobs = extract_jobs_with_recipe(html, "https://example.com", _recipe())

    assert jobs[0].rate == "Not listed"


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


def test_pagination_discovery_detects_listing_expansion_links_without_rss() -> None:
    html = """
    <section class="jobs">
      <a href="/categories/all-other-remote-jobs.rss"></a>
      <a href="/categories/all-other-remote-jobs">View all 47 All Other Remote jobs</a>
      <a href="/categories/remote-full-stack-programming-jobs">View all 55 Full-Stack Programming jobs</a>
    </section>
    """

    links = discover_pagination_links(html, "https://weworkremotely.com/remote-jobs/search")

    assert [link.url for link in links] == [
        "https://weworkremotely.com/categories/all-other-remote-jobs",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs",
    ]


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


def test_recipe_flags_pagination_pages_that_return_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    page_1 = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2><p class="summary">ABAP listing one with useful context.</p></div>
    <a class="page-numbers" href="https://example.com/jobs/page/2/">2</a>
    """
    page_2 = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2><p class="summary">ABAP listing one with useful context.</p></div>
    """

    def fake_fetch(url: str, timeout_seconds: int):
        if url.endswith("/page/2/"):
            return page_2, url, []
        return page_1, "https://example.com/jobs", []

    recipe = _recipe(
        pagination=PaginationRecipe(
            page_link_selector="a.page-numbers",
            max_pages=2,
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

    checks = {check.capability: check for check in result.capability_checks}
    assert [job.title for job in result.jobs] == ["SAP ABAP Consultant"]
    assert result.pagination_fetch_count == 1
    assert result.pagination_duplicate_page_count == 1
    assert result.pagination_duplicate_ratio == 1.0
    assert checks["pagination_strategy"].status == "fail"
    assert checks["pagination_navigation"].status == "fail"
    assert checks["pagination_duplicate_pages"].status == "fail"
    assert "repeated postings" in checks["pagination_strategy"].detail
    assert "produced no new jobs" in checks["pagination_navigation"].detail
    assert any("duplicate listing" in warning for warning in result.warnings)


def test_url_pagination_skips_links_back_to_start_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def job_cards(start: int, stop: int) -> str:
        return "\n".join(
            (
                f'<div class="job-card"><h2><a class="job-link" href="/jobs/sap-{index}">'
                f'SAP Consultant {index}</a></h2><p class="summary">SAP listing {index} with useful context.</p></div>'
            )
            for index in range(start, stop + 1)
        )

    page_1 = job_cards(1, 15) + '<a class="page-numbers" href="/jobs/page/2/">2</a>'
    page_2 = (
        job_cards(16, 30)
        + '<a class="page-numbers" href="/jobs/page/3/">3</a>'
        + '<a class="page-numbers" href="/jobs/">Previous</a>'
    )
    page_3 = job_cards(31, 40) + '<a class="page-numbers" href="/jobs/">Back to first page</a>'

    def fake_fetch(url: str, timeout_seconds: int):
        if url.endswith("/page/2/"):
            return page_2, url, []
        if url.endswith("/page/3/"):
            return page_3, url, []
        return page_1, "https://example.com/jobs/", []

    recipe = _recipe(
        pagination=PaginationRecipe(
            page_link_selector="a.page-numbers",
            max_pages=4,
            request_delay_seconds=0,
        )
    )
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch)

    result = extract_jobs_with_recipe_from_url(
        "https://example.com/jobs/",
        recipe,
        fetch_pagination=True,
        fetch_details=False,
        pagination_page_limit=0,
    )

    checks = {check.capability: check for check in result.capability_checks}
    assert len(result.jobs) == 40
    assert result.pagination_fetch_attempts == [
        "https://example.com/jobs/page/2/",
        "https://example.com/jobs/page/3/",
    ]
    assert result.pagination_duplicate_page_count == 0
    assert result.listing_duplicate_count == 0
    assert result.warnings == []
    assert checks["pagination_strategy"].status == "pass"
    assert checks["pagination_duplicate_pages"].status == "pass"


def test_url_pagination_allows_duplicate_overlap_when_page_adds_new_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    page_1 = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-1">SAP ABAP Consultant</a></h2><p class="summary">ABAP listing one with useful context.</p></div>
    <a class="page-numbers" href="https://example.com/jobs/page/2/">2</a>
    """
    page_2 = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-1">SAP ABAP Consultant</a></h2><p class="summary">ABAP listing one with useful context.</p></div>
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-2">SAP Basis Consultant</a></h2><p class="summary">Basis listing two with useful context.</p></div>
    """

    def fake_fetch(url: str, timeout_seconds: int):
        if url.endswith("/page/2/"):
            return page_2, url, []
        return page_1, "https://example.com/jobs", []

    recipe = _recipe(
        pagination=PaginationRecipe(
            page_link_selector="a.page-numbers",
            max_pages=2,
            request_delay_seconds=0,
        )
    )
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch)

    result = extract_jobs_with_recipe_from_url(
        "https://example.com/jobs",
        recipe,
        fetch_pagination=True,
        fetch_details=False,
    )

    checks = {check.capability: check for check in result.capability_checks}
    assert [job.title for job in result.jobs] == ["SAP ABAP Consultant", "SAP Basis Consultant"]
    assert result.pagination_duplicate_page_count == 1
    assert result.pagination_duplicate_ratio == 0.5
    assert result.warnings == []
    assert checks["pagination_strategy"].status == "pass"
    assert checks["pagination_navigation"].status == "pass"
    assert checks["pagination_duplicate_pages"].status == "pass"
    assert "duplicate listing overlap" in checks["pagination_navigation"].detail
    assert "every fetched page added new jobs" in checks["pagination_duplicate_pages"].detail


def test_connected_session_passes_source_access_even_when_pagination_strategy_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_1 = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2><p class="summary">ABAP listing one with useful context.</p></div>
    <a class="page-numbers" href="https://example.com/jobs/page/2/">2</a>
    """
    page_2 = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2><p class="summary">ABAP listing one with useful context.</p></div>
    """

    def fake_fetch(url: str, timeout_seconds: int, **kwargs):
        if url.endswith("/page/2/"):
            return page_2, url, []
        return page_1, "https://example.com/jobs", []

    recipe = _recipe(
        access=AccessRecipe(requires_session=True, session_scope="example.com"),
        pagination=PaginationRecipe(
            page_link_selector="a.page-numbers",
            max_pages=2,
            request_delay_seconds=0,
        ),
    )
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch)

    result = extract_jobs_with_recipe_from_url(
        "https://example.com/jobs",
        recipe,
        fetch_pagination=True,
        pagination_page_limit=2,
        session_state_path=Path("sources/sessions/example.storage-state.json"),
    )

    checks = {check.capability: check for check in result.capability_checks}
    assert checks["source_access"].status == "pass"
    assert checks["source_access"].observed is True
    assert checks["pagination_strategy"].status == "fail"
    assert "Pagination still returned duplicate pages" in checks["source_access"].detail


def test_connected_session_fails_source_access_when_page_still_shows_login_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = """
    <main>
      <div class="registration-modal">Create your account to see more results</div>
      <div class="modal-backdrop show"></div>
      <div class="job-card">
        <h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2>
        <p class="summary">ABAP listing one with useful context.</p>
      </div>
    </main>
    """

    def fake_fetch(url: str, timeout_seconds: int, **kwargs):
        return html, "https://example.com/jobs", []

    recipe = _recipe(access=AccessRecipe(requires_session=True, session_scope="example.com"))
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch)

    result = extract_jobs_with_recipe_from_url(
        "https://example.com/jobs",
        recipe,
        session_state_path=Path("sources/sessions/example.storage-state.json"),
    )

    checks = {check.capability: check for check in result.capability_checks}
    assert result.source_access_session_used is True
    assert result.source_access_login_gate_detected is True
    assert checks["source_access"].status == "fail"
    assert checks["source_access"].observed is False
    assert "still showed a sign-in or registration gate" in checks["source_access"].detail


def test_ajax_pagination_template_fetches_payload_and_passes_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    page_1 = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2><p class="summary">ABAP listing one with useful context.</p></div>
    """
    page_2_payload = {
        "html": """
        <div class="job-card"><h2><a class="job-link" href="/jobs/sap-basis">SAP Basis Consultant</a></h2><p class="summary">Basis listing two with useful context.</p></div>
        """
    }
    calls = []

    def fake_fetch(url: str, timeout_seconds: int):
        calls.append(url)
        if "page=2" in url:
            return json.dumps(page_2_payload), url, []
        return page_1, "https://example.com/jobs", []

    recipe = _recipe(
        pagination=PaginationRecipe(
            strategy="ajax",
            ajax_url_template="/api/jobs?page={page}",
            max_pages=2,
            request_delay_seconds=0,
        )
    )
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch)

    result = extract_jobs_with_recipe_from_url("https://example.com/jobs", recipe, fetch_pagination=True)

    checks = {check.capability: check for check in result.capability_checks}
    pagination_step = next(step for step in result.steps if step.phase == "Pagination detection")
    assert calls == ["https://example.com/jobs", "https://example.com/api/jobs?page=2"]
    assert [job.title for job in result.jobs] == ["SAP ABAP Consultant", "SAP Basis Consultant"]
    assert pagination_step.status == "completed"
    assert "Proof fetched 1 pagination page(s)" in pagination_step.detail
    assert checks["ajax_pagination"].status == "pass"
    assert checks["pagination_strategy"].status == "pass"


def test_browser_click_pagination_fetch_marks_detection_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    page_1 = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2><p class="summary">ABAP listing one with useful context.</p></div>
    <button class="pagination-next" type="button">Next page</button>
    """

    def fake_fetch(url: str, timeout_seconds: int):
        return page_1, "https://example.com/jobs", []

    def fake_browser_click_fetch(
        start_url: str,
        recipe: JobBoardRecipe,
        *,
        timeout_seconds: int,
        max_pages: int | None,
        existing_jobs: list[Job],
        job_limit: int | None,
        use_recipe_card_limit: bool,
        session_state_path: str | Path | None = None,
        progress_callback=None,
        step_collector=None,
    ):
        jobs = [
            *existing_jobs,
            Job(title="SAP Basis Consultant", url="https://example.com/jobs/sap-basis", source_confidence="recipe"),
        ]
        return [], jobs, ["browser-click:https://example.com/jobs?page=2"], []

    recipe = _recipe(
        pagination=PaginationRecipe(
            strategy="browser_click",
            click_selector=".pagination-next",
            max_pages=2,
            request_delay_seconds=0,
        )
    )
    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch)
    monkeypatch.setattr(
        "job_agent.services.job_board_recipe_service._fetch_browser_click_pagination_job_pages",
        fake_browser_click_fetch,
    )

    result = extract_jobs_with_recipe_from_url("https://example.com/jobs", recipe, fetch_pagination=True)

    checks = {check.capability: check for check in result.capability_checks}
    pagination_step = next(step for step in result.steps if step.phase == "Pagination detection")
    assert [job.title for job in result.jobs] == ["SAP ABAP Consultant", "SAP Basis Consultant"]
    assert pagination_step.status == "completed"
    assert "Proof fetched 1 pagination page(s)" in pagination_step.detail
    assert checks["browser_click_pagination"].status == "pass"
    assert checks["pagination_strategy"].status == "pass"


def test_click_only_pagination_controls_require_browser_click_strategy() -> None:
    html = """
    <div class="job-card"><h2><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></h2><p class="summary">ABAP listing one with useful context.</p></div>
    <button class="pagination-next" type="button">Next page</button>
    """

    result = extract_jobs_with_recipe_from_html(html, "https://example.com/jobs", _recipe())

    checks = {check.capability: check for check in result.capability_checks}
    assert result.interactive_pagination_control_count == 1
    assert checks["pagination_strategy"].status == "fail"
    assert "browser-click pagination" in checks["pagination_strategy"].detail
    assert checks["browser_click_pagination"].status == "fail"


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


def test_pagination_detection_dedupes_falsey_query_variants() -> None:
    html = """
    <div class='paginator'>
      <a href='/projects?query=sap&hideAppliedProjects=&showHiddenProjects=&pagenr=2#list'>2</a>
      <a href='/projects?query=sap&hideAppliedProjects=0&showHiddenProjects=0&pagenr=2#list'>2</a>
      <a href='/projects?query=sap&hideAppliedProjects=0&showHiddenProjects=0&pagenr=3#list'>3</a>
    </div>
    """
    recipe = _recipe(pagination=PaginationRecipe(page_link_selector='a[href*="pagenr="]', max_pages=4))

    links = find_pagination_links(html, "https://www.freelancermap.com/projects", recipe)

    assert [(link.label, link.url) for link in links] == [
        (
            "2",
            "https://www.freelancermap.com/projects?query=sap&hideAppliedProjects=&showHiddenProjects=&pagenr=2#list",
        ),
        (
            "3",
            "https://www.freelancermap.com/projects?query=sap&hideAppliedProjects=0&showHiddenProjects=0&pagenr=3#list",
        ),
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


def test_project_recipe_loader_resolves_sources_path_in_new_layout(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "app").mkdir(parents=True)
    (root / "setup").mkdir()
    recipe_path = root / "user" / "sources" / "recipes" / "experimental" / "example.yaml"
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text(
        "source_name: Example\nlisting:\n  card_selector: article\n  title_selector: h2\n  link_selector: a\n",
        encoding="utf-8",
    )

    recipe = load_project_job_board_recipe(root, "sources/recipes/experimental/example.yaml")

    assert recipe.source_name == "Example"


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


def _api_recipe(detail: DetailRecipe | None = None) -> JobBoardRecipe:
    return JobBoardRecipe(
        source_name="API Board",
        start_url="https://example.com/en-gb/job-search/",
        listing_api=ApiRequestRecipe(
            method="POST",
            url="https://example.com/api/search",
            body={"resultSize": 20, "resultFrom": 0, "resultPage": 0},
            results_path="result.results",
            total_path="result.hits",
            fields=ApiFieldMapping(
                title="title",
                url_template="https://example.com/en-gb/job/{slug}/{jobReference}/",
                location="location",
                remote="remoteWorkingAvailable",
                rate="salaryText",
                workload="jobType",
                posted_date="postDate",
                description_html="description",
                job_id="jobReference",
            ),
            pagination=ApiPaginationRecipe(
                strategy="offset",
                offset_param="resultFrom",
                offset_start=0,
                page_param="resultPage",
                page_start=0,
                page_size_param="resultSize",
                page_size=20,
                max_pages=3,
                request_delay_seconds=0,
            ),
        ),
        accept=AcceptRecipe(url_contains=["/job/"]),
        reject=RejectRecipe(),
        detail=detail or DetailRecipe(),
        pagination=PaginationRecipe(),
        patterns=PatternsRecipe(),
    )


def _recipe(
    card_selector: str = ".job-card",
    access: AccessRecipe | None = None,
    detail: DetailRecipe | None = None,
    mode: str = "static_html",
    pagination: PaginationRecipe | None = None,
    patterns: PatternsRecipe | None = None,
) -> JobBoardRecipe:
    return JobBoardRecipe(
        source_name="Test Board",
        mode=mode,
        access=access or AccessRecipe(),
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
