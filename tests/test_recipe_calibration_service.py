from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_agent.services.job_board_recipe_service import (
    extract_job_detail_from_html,
    extract_jobs_with_recipe,
    job_board_recipe_from_mapping,
)
from job_agent.services.recipe_calibration_service import (
    audit_recipe_selectors,
    build_recipe_blueprint,
    capture_recipe_calibration,
    discover_ajax_pagination_templates,
    discover_api_access_candidates,
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


def test_blueprint_rejects_media_project_links_as_non_job_evidence() -> None:
    html = """
    <!doctype html>
    <html><body>
      <footer>
        <div class="footer-disclaimer">
          <a href="/-/media/project/manpowergroup/legal/reporting.pdf">Reporting violations</a>
          <a href="/en/privacy-policy">Privacy Policy</a>
          <a href="/en/all-jobs">View All Jobs</a>
        </div>
      </footer>
    </body></html>
    """

    blueprint = build_recipe_blueprint(html, "https://www.experis.pl/en/search?page=1&searchKeyword=SAP")
    candidates = discover_candidate_elements(html, max_candidates=10)

    assert blueprint["status"] == "not_recommended"
    assert all(candidate.likely_noise for candidate in candidates)


def test_blueprint_detects_experis_rendered_jobb_cards() -> None:
    html = """
    <!doctype html>
    <html><body>
      <section class="search-job-results">
        <div id="job_0">
          <div class="job-search-result card">
            <div class="card-body">
              <div class="date">10/06/2026</div>
              <div class="job-position">
                <h2 class="title experis">
                  <a href="/en/jobb/27021/sap-pp-pi-consultant">SAP PP-PI Consultant</a>
                </h2>
              </div>
              <div class="job-details"><div class="location">Wroclaw, Dolnoslaskie</div><div class="type">Contract</div></div>
              <div class="job-description"><p class="excerpt">SAP PP-PI contract role with S/4HANA delivery context.</p></div>
            </div>
          </div>
        </div>
        <div id="job_1">
          <div class="job-search-result card">
            <div class="card-body">
              <div class="date">09/06/2026</div>
              <div class="job-position">
                <h2 class="title experis">
                  <a href="/en/jobb/26934/sap-solution-manager">SAP Solution Manager</a>
                </h2>
              </div>
              <div class="job-details"><div class="location">Warszawa</div><div class="type">Contract</div></div>
              <div class="job-description"><p class="excerpt">SAP Solution Manager project role.</p></div>
            </div>
          </div>
        </div>
        <div id="job_2">
          <div class="job-search-result card">
            <div class="card-body">
              <div class="date">31/05/2026</div>
              <div class="job-position">
                <h2 class="title experis">
                  <a href="/en/jobb/25755/sap-s4hana-developer">SAP S4HANA Developer</a>
                </h2>
              </div>
              <div class="job-details"><div class="location">Remote</div><div class="type">Contract</div></div>
              <div class="job-description"><p class="excerpt">ABAP development in an S/4HANA programme.</p></div>
            </div>
          </div>
        </div>
        <div class="search-pagination-wrap">
          <a class="page-link" href="undefined?page=1" aria-label="Page 1">1</a>
          <a class="page-link" href="undefined?page=2" aria-label="Go to page 2">2</a>
          <a class="page-link" href="undefined?page=3" aria-label="Go to page 3">3</a>
          <a class="page-link" href="undefined?page=9" aria-label="Go to page 9">9</a>
          <a class="page-link more" href="undefined?page=2" aria-label="Next">Next</a>
        </div>
      </section>
    </body></html>
    """

    blueprint = build_recipe_blueprint(
        html,
        "https://www.experis.pl/en/search?page=1&searchKeyword=SAP",
        capture_mode="rendered_html",
    )

    assert blueprint["status"] == "draft"
    recipe = blueprint["recipe"]
    assert recipe["mode"] == "rendered_html"
    assert recipe["listing"]["card_selector"] == "div.card-body"
    assert recipe["listing"]["title_selector"] == "h2 a"
    assert recipe["listing"]["description_selector"] == ".job-description"
    assert recipe["accept"]["url_contains"] == ["/jobb/"]
    assert recipe["pagination"]["strategy"] == "url"
    assert recipe["pagination"]["page_link_selector"] == 'a.page-link[href*="page="]'
    assert recipe["pagination"]["next_selector"] == 'a.page-link[href*="page="][aria-label*="Next"]'
    assert recipe["pagination"]["max_pages"] == 9


def test_blueprint_uses_heading_title_when_job_link_is_empty_overlay() -> None:
    html = """
    <!doctype html>
    <html><body>
      <ul class="job-list">
        <li class="single-job">
          <a class="main" href="https://next-ventures.com/jobs/sap-program-manager/"></a>
          <div class="outer">
            <span class="ref">Ref: #73942</span>
            <h4>SAP Program Manager - 12 month contract</h4>
            <ul class="meta">
              <li><p><b>Dallas, United States</b></p></li>
              <li><p><b>SAP Technology Jobs</b></p></li>
              <li><p><b>SAP</b></p></li>
              <li><p><b>Contract</b></p></li>
            </ul>
          </div>
        </li>
        <li class="single-job">
          <a class="main" href="https://next-ventures.com/jobs/sap-is-retail-consultant/"></a>
          <div class="outer">
            <span class="ref">Ref: #73939</span>
            <h4>SAP IS-Retail Consultant</h4>
            <ul class="meta">
              <li><p><b>Barcelona, Spain</b></p></li>
              <li><p><b>SAP Technology Jobs</b></p></li>
              <li><p><b>SAP</b></p></li>
              <li><p><b>Contract</b></p></li>
            </ul>
          </div>
        </li>
      </ul>
    </body></html>
    """

    blueprint = build_recipe_blueprint(html, "https://next-ventures.com/practices/sap-recruitment/sap-jobs/")
    recipe = blueprint["recipe"]

    assert blueprint["status"] == "draft"
    assert recipe["listing"]["card_selector"] == "li.single-job"
    assert recipe["listing"]["title_selector"] == "h4"
    assert recipe["listing"]["link_selector"] == 'a.main[href*="/jobs/"]'
    jobs = extract_jobs_with_recipe(html, recipe["start_url"], job_board_recipe_from_mapping(recipe))
    assert [job.title for job in jobs] == [
        "SAP Program Manager - 12 month contract",
        "SAP IS-Retail Consultant",
    ]


def test_blueprint_detects_job_role_listing_urls() -> None:
    html = """
    <!doctype html>
    <html><body>
      <div class="row">
        <div class="col-12 col-md-4">Keyword Search SAP SD Consultant SAP Fiori Lead</div>
        <div class="col-12 col-md-8">
          <div class="facetwp-template">
            <div class="job-item">
              <div class="row no-gutters">
                <div class="col-12 job-title">
                  <h2><a href="https://www.sapcontractors.com/job-role/sap-program-manager-2/">SAP Program Manager</a></h2>
                </div>
                <div class="left">Engagement Type: Projects and support... <a href="https://www.sapcontractors.com/job-role/sap-program-manager-2/">See full description</a></div>
                <div class="job_type"><span>Contract</span></div>
                <div class="start_date"><span>Immediately</span></div>
                <div class="salary"><span>$140/hr</span></div>
                <div class="location"><span>Houston, TX</span>, <span>USA</span></div>
              </div>
            </div>
            <div class="job-item">
              <div class="row no-gutters">
                <div class="col-12 job-title">
                  <h2><a href="https://www.sapcontractors.com/job-role/sap-project-manager/">SAP Project Manager</a></h2>
                </div>
                <div class="left">Engagement Type: Projects and support... <a href="https://www.sapcontractors.com/job-role/sap-project-manager/">See full description</a></div>
                <div class="job_type"><span>Contract</span></div>
                <div class="start_date"><span>Immediately</span></div>
                <div class="salary"><span>$120/hr</span></div>
                <div class="location"><span>Houston, TX</span>, <span>USA</span></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </body></html>
    """

    blueprint = build_recipe_blueprint(html, "https://www.sapcontractors.com/search-jobs/?fwp_keyword=")
    recipe = blueprint["recipe"]

    assert blueprint["status"] == "draft"
    assert recipe["listing"]["card_selector"] == "div.job-item"
    assert recipe["listing"]["title_selector"] == "h2 a"
    assert recipe["listing"]["link_selector"] == "h2 a"
    assert recipe["listing"]["description_selector"] == ".left"
    assert recipe["listing"]["workload_selector"] == ".job_type"
    assert recipe["listing"]["start_date_selector"] == ".start_date"
    assert recipe["listing"]["rate_selector"] == ".salary"
    assert recipe["listing"]["location_selector"] == ".location"
    assert recipe["accept"]["url_contains"] == ["/job-role/"]
    jobs = extract_jobs_with_recipe(html, recipe["start_url"], job_board_recipe_from_mapping(recipe))
    assert len(jobs) == 2
    assert jobs[0].title == "SAP Program Manager"
    assert jobs[0].workload == "Contract"
    assert jobs[0].rate == "$140/hr"


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


def test_calibration_auto_mode_keeps_static_when_listing_evidence_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_static(url: str, timeout_seconds: int):
        calls.append("static")
        return CALIBRATION_HTML, url, []

    def fake_rendered(url: str, timeout_seconds: int):
        calls.append("rendered")
        raise AssertionError("Rendered capture should not run when static HTML has enough listing evidence.")

    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_static_html", fake_static)
    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_rendered_html", fake_rendered)

    result = capture_recipe_calibration(
        "https://example.com/jobs",
        root=tmp_path,
        rendered=None,
        max_candidates=5,
        capture_detail=False,
    )

    assert calls == ["static"]
    assert result.capture_mode == "static_html"


def test_calibration_auto_mode_falls_back_to_rendered_when_static_has_no_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    rendered_html = """
    <main>
      <article class="job-card">
        <a href="/jobs/sap-abap">SAP ABAP Consultant</a>
        <p>SAP contract role.</p>
      </article>
      <article class="job-card">
        <a href="/jobs/sap-basis">SAP Basis Consultant</a>
        <p>SAP contract role.</p>
      </article>
    </main>
    """

    def fake_static(url: str, timeout_seconds: int):
        calls.append("static")
        return "<html><body><div id='app'></div></body></html>", url, []

    def fake_rendered(url: str, timeout_seconds: int):
        calls.append("rendered")
        return rendered_html, url, []

    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_static_html", fake_static)
    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_rendered_html", fake_rendered)

    result = capture_recipe_calibration(
        "https://example.com/jobs",
        root=tmp_path,
        rendered=None,
        max_candidates=5,
        capture_detail=False,
    )

    assert calls == ["static", "rendered"]
    assert result.capture_mode == "rendered_html"
    assert result.recipe_extracted_count == 0
    assert "rendered_html" in (result.artifact_dir / "selector-report.json").read_text(encoding="utf-8")


def test_calibration_warns_for_client_rendered_job_search_with_default_country_and_blocked_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static_html = """
    <html><body>
      <div id="hitsList"></div>
      <script id="result-row" type="text/x-handlebars-template">
        <li class="job-search__item"><h2>{{title}}</h2><a href="/en-gb/job/{{slug}}/{{jobReference}}/"></a></li>
      </script>
      <script>
        var queryParams = new URLSearchParams(window.location.search);
        var urlCountry = queryParams.get('country') || FacHelpers.getCountryByCulture(jobLanguage);
        var initParams = { apiUrl: 'https://api.example.test/', country: urlCountry };
        jobSearch.init(initParams);
      </script>
    </body></html>
    """
    blocked_rendered_html = """
    <html><head><title>Attention Required! | Cloudflare</title></head>
      <body>Sorry, you have been blocked. Cloudflare Ray ID: test</body>
    </html>
    """

    def fake_static(url: str, timeout_seconds: int):
        return static_html, url, []

    def fake_rendered(url: str, timeout_seconds: int):
        return blocked_rendered_html, url, []

    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_static_html", fake_static)
    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_rendered_html", fake_rendered)

    result = capture_recipe_calibration(
        "https://www.globalenterprisepartners.com/en-gb/job-search/?industry=SAP&type=Contract&searchRadius=20mi",
        root=tmp_path,
        capture_detail=False,
    )

    assert result.capture_mode == "static_html"
    assert any("client-side job-search templates" in warning for warning in result.warnings)
    assert any("default a missing country filter" in warning for warning in result.warnings)
    assert any("blocked by site protection" in warning for warning in result.warnings)


def test_calibration_discovers_page_declared_sthree_api_without_default_country_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = _sthree_shell_html()
    calls = []

    def fake_static(url: str, timeout_seconds: int):
        return html, url, []

    def fake_json_api(**kwargs):
        calls.append(kwargs)
        country = kwargs["body"].get("country") or []
        total = 2 if country else 95
        count = 2 if country else 20
        return _sthree_payload(total=total, count=count), kwargs["url"], []

    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_static_html", fake_static)
    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_json_api", fake_json_api)

    result = capture_recipe_calibration(
        "https://www.globalenterprisepartners.com/en-gb/job-search/?industry=SAP&type=Contract&searchRadius=20mi",
        root=tmp_path,
        rendered=False,
        capture_detail=False,
    )

    report = json.loads((result.artifact_dir / "selector-report.json").read_text(encoding="utf-8"))
    api_candidate = report["observed_api_candidates"][0]
    blueprint_recipe = report["recipe_blueprint"]["recipe"]
    assert calls[0]["body"]["country"] == []
    assert calls[0]["body"]["industry"] == ["SAP"]
    assert calls[0]["body"]["type"] == ["Contract"]
    assert api_candidate["total_count"] == 95
    assert api_candidate["record_count"] == 20
    assert (result.artifact_dir / "api-listing-response-1.json").exists()
    assert "listing_api" in blueprint_recipe
    assert blueprint_recipe["listing_api"]["fields"]["url_template"].endswith("/job/{slug}/{jobReference}/")

    country_artifact = tmp_path / "country-api"
    country_artifact.mkdir()
    country_candidates, _warnings = discover_api_access_candidates(
        html,
        "https://www.globalenterprisepartners.com/en-gb/job-search/?industry=SAP&type=Contract&searchRadius=20mi&country=United+Kingdom",
        artifact_dir=country_artifact,
    )
    assert calls[-1]["body"]["country"] == ["United Kingdom"]
    assert country_candidates[0]["total_count"] == 2


def test_calibration_writes_ajax_pagination_observations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <main>
      <article class="job-card"><a href="/jobs/sap-1">SAP ABAP Consultant</a></article>
      <button data-url="/api/jobs?query=sap&page=2">Load more</button>
    </main>
    """

    def fake_static(url: str, timeout_seconds: int):
        return html, url, []

    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_static_html", fake_static)

    result = capture_recipe_calibration("https://example.com/jobs", root=tmp_path, max_candidates=5)

    report = (result.artifact_dir / "selector-report.json").read_text(encoding="utf-8")
    assert "observed_ajax_pagination_templates" in report
    assert "https://example.com/api/jobs?query=sap&page={page}" in report


def test_calibration_passes_session_state_to_rendered_listing_and_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing_html = """
    <main>
      <article class="job-card">
        <a href="/jobs/sap-1">SAP ABAP Consultant</a>
        <p>SAP contract role with useful listing context.</p>
      </article>
    </main>
    """
    detail_html = """
    <main class="job-single">
      <h1>SAP ABAP Consultant</h1>
      <div class="job-description">Detailed SAP ABAP contract role supporting an S/4HANA programme.</div>
    </main>
    """
    state_path = tmp_path / "source.storage-state.json"
    state_path.write_text('{"cookies": []}', encoding="utf-8")
    calls: list[tuple[str, Path | None]] = []

    def fake_rendered(url: str, timeout_seconds: int, *, session_state_path=None):
        calls.append((url, Path(session_state_path) if session_state_path else None))
        if url.endswith("/jobs/sap-1"):
            return detail_html, url, []
        return listing_html, url, []

    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_rendered_html", fake_rendered)

    result = capture_recipe_calibration(
        "https://example.com/jobs",
        rendered=True,
        root=tmp_path,
        max_candidates=5,
        session_state_path=state_path,
        source_session_scope="example.com",
    )

    assert calls == [
        ("https://example.com/jobs", state_path),
        ("https://example.com/jobs/sap-1", state_path),
    ]
    report = json.loads(result.selector_report_path.read_text(encoding="utf-8"))
    assert report["source_session_used"] is True
    assert report["source_session_scope"] == "example.com"
    assert str(state_path) not in json.dumps(report)


def test_blueprint_detects_whitehall_style_listing_pagination_and_detail() -> None:
    blueprint = build_recipe_blueprint(
        WHITEHALL_LIST_HTML,
        "https://www.whitehallresources.com/sap-jobs/",
        detail_html=WHITEHALL_DETAIL_HTML,
        detail_url="https://www.whitehallresources.com/job/sap-eam/",
    )

    recipe = blueprint["recipe"]

    assert recipe["listing"]["card_selector"] == "div.job-item"
    assert recipe["listing"]["title_selector"] == "h3 a"
    assert recipe["pagination"]["strategy"] == "url"
    assert recipe["pagination"]["page_link_selector"] == "a.page-numbers"
    assert recipe["pagination"]["next_selector"] == "a.next.page-numbers"
    assert recipe["pagination"]["max_pages"] == 4
    assert recipe["detail"]["follow"] is True
    assert recipe["detail"]["use_json_ld"] is True
    assert ".job-single h1" in recipe["detail"]["title_selector"]


def test_blueprint_detects_freelancermap_link_rel_and_embedded_pagination() -> None:
    html = """
    <!doctype html>
    <html><head>
      <link rel="next" href="https://www.freelancermap.com/projects?query=sap&pagenr=2">
    </head><body>
      <main>
        <div class="project-card">
          <div class="project-info">
            <div class="mg-b-display-m line-height-base">K2 Partnering Solutions</div>
            <div><a class="h3 no-underline" href="/project/sap-data-migration-consultant-3001622">SAP Data Migration Consultant</a></div>
            <div data-testid="city">lisboa, Portugal</div>
            <div data-testid="remoteInPercent">100% remote</div>
            <div data-testid="type">Freelance</div>
            <div data-testid="beginningText">9/2026</div>
            <span data-testid="created">19.05.2026</span>
          </div>
        </div>
        <div class="project-card">
          <div class="project-info">
            <div class="mg-b-display-m line-height-base">ForTech Consulting GmbH</div>
            <div><a class="h3 no-underline" href="/project/sap-fi-consultant">SAP FI Consultant</a></div>
            <div data-testid="city">Lisbon, Portugal</div>
          </div>
        </div>
      </main>
      <script type="application/json">
      {"initialPagination":"<div class='paginator'><a href='/projects?query=sap&pagenr=2#list'>2</a><a href='/projects?query=sap&pagenr=3#list'>3</a><a href='/projects?query=sap&pagenr=4#list'>4</a></div>"}
      </script>
      <div class="registration-modal">Sign up free to see more results</div>
    </body></html>
    """

    blueprint = build_recipe_blueprint(html, "https://www.freelancermap.com/projects?query=sap&pagenr=1")
    recipe = blueprint["recipe"]

    assert recipe["listing"]["card_selector"] == "div.project-card"
    assert recipe["listing"]["company_selector"] == "div.project-info > div.mg-b-display-m:first-child"
    assert recipe["listing"]["location_selector"] == '[data-testid="city"]'
    assert recipe["listing"]["remote_selector"] == '[data-testid="remoteInPercent"]'
    assert recipe["listing"]["posted_date_selector"] == '[data-testid="created"]'
    assert recipe["listing"]["start_date_selector"] == '[data-testid="beginningText"]'
    assert recipe["pagination"]["strategy"] == "url"
    assert recipe["pagination"]["page_link_selector"] == 'a[href*="pagenr="]'
    assert recipe["pagination"]["next_selector"] == 'link[rel="next"]'
    assert recipe["pagination"]["max_pages"] == 4
    assert recipe["access"]["requires_session"] is True
    assert recipe["access"]["session_scope"] == "www.freelancermap.com"


def test_blueprint_detects_click_only_pagination_strategy() -> None:
    html = """
    <!doctype html>
    <html><body>
      <main>
        <article class="job-card">
          <h2><a href="/jobs/sap-abap">SAP ABAP Consultant</a></h2>
          <p>Contract role with RAP and OData.</p>
        </article>
        <button class="pagination-next" type="button" aria-label="Next page">Next</button>
      </main>
    </body></html>
    """

    blueprint = build_recipe_blueprint(html, "https://example.com/jobs")
    recipe = blueprint["recipe"]

    assert recipe["pagination"]["strategy"] == "browser_click"
    assert recipe["pagination"]["click_selector"] == ".pagination-next"


def test_blueprint_detects_ajax_pagination_template() -> None:
    html = """
    <!doctype html>
    <html><body>
      <main>
        <article class="job-card">
          <a class="job-link" href="/jobs/sap-1">SAP ABAP Consultant</a>
          <p>SAP contract role.</p>
        </article>
        <article class="job-card">
          <a class="job-link" href="/jobs/sap-2">SAP Basis Consultant</a>
          <p>SAP contract role.</p>
        </article>
        <button class="load-more" data-url="/api/jobs?query=sap&page=2">Load more</button>
      </main>
    </body></html>
    """

    blueprint = build_recipe_blueprint(html, "https://example.com/jobs")
    recipe = blueprint["recipe"]

    assert recipe["pagination"]["strategy"] == "ajax"
    assert recipe["pagination"]["ajax_url_template"] == "https://example.com/api/jobs?query=sap&page={page}"
    assert discover_ajax_pagination_templates(html, "https://example.com/jobs")[0]["evidence"] == "button[data-url]"


def test_blueprint_uses_table_headers_and_detail_labels_semantically() -> None:
    overview_html = """
    <!doctype html>
    <html><body>
      <table>
        <thead>
          <tr>
            <th>Position</th>
            <th>Category</th>
            <th>Location</th>
            <th>Application deadline</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><a href="https://accuro.dk/en/freelance_projects/senior-iam/">Senior IAM / IGA Engineer</a></td>
            <td>IT</td>
            <td>Copenhagen</td>
            <td>28 May, 2026</td>
          </tr>
          <tr>
            <td><a href="https://accuro.dk/en/freelance_projects/test-manager/">Test Manager</a></td>
            <td>Test Management &amp; Test</td>
            <td>København</td>
            <td>26 May, 2026</td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """
    detail_html = """
    <!doctype html>
    <html><body>
      <section class="task_detail_wrapp">
        <h1>Senior IAM / IGA Engineer</h1>
        <div class="jb_det_box">
          <div class="dato_div_sec">
            <div class="dato_wrapp">Start date:&nbsp;<span>01 Jun, 2026</span></div>
            <div class="dato_wrapp">End date:&nbsp;<span>30 Nov, 2026</span></div>
          </div>
          <div class="dato_div_sec">
            <div class="dato_wrapp">Application deadline:&nbsp;<span>28 May, 2026</span></div>
            <div class="dato_wrapp">Location:&nbsp;<span>Copenhagen</span></div>
          </div>
        </div>
        <div class="job_txt_wrapp"><div class="des_wrapp">Description: Long IAM delivery description.</div></div>
      </section>
    </body></html>
    """

    blueprint = build_recipe_blueprint(
        overview_html,
        "https://accuro.dk/en/consultant/freelance-projects/",
        detail_html=detail_html,
        detail_url="https://accuro.dk/en/freelance_projects/senior-iam/",
    )
    recipe = blueprint["recipe"]

    assert recipe["listing"]["card_selector"] == "tbody tr"
    assert recipe["listing"]["location_selector"] == "td:nth-of-type(3)"
    assert "workload_selector" not in recipe["listing"]
    assert "posted_date_selector" not in recipe["listing"]
    assert recipe["detail"]["location_selector"].endswith(".dato_wrapp:nth-of-type(2) > span")
    assert recipe["detail"]["start_date_selector"].endswith(".dato_wrapp:nth-of-type(1) > span")
    assert any("Application deadline" in warning for warning in blueprint["warnings"])
    assert any("Category" in warning for warning in blueprint["warnings"])

    parsed_recipe = job_board_recipe_from_mapping(recipe)
    detail_job = extract_job_detail_from_html(
        detail_html,
        "https://accuro.dk/en/freelance_projects/senior-iam/",
        parsed_recipe,
    )

    assert detail_job.location == "Copenhagen"
    assert detail_job.start_date == "01 Jun, 2026"


def test_calibration_captures_one_detail_sample_and_blueprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_static(url: str, timeout_seconds: int):
        calls.append(url)
        if "/job/" in url:
            return WHITEHALL_DETAIL_HTML, url, []
        return WHITEHALL_LIST_HTML, url, []

    monkeypatch.setattr("job_agent.services.recipe_calibration_service._fetch_static_html", fake_static)

    result = capture_recipe_calibration(
        "https://www.whitehallresources.com/sap-jobs/",
        root=tmp_path,
        max_candidates=10,
    )

    report = (result.artifact_dir / "selector-report.json").read_text(encoding="utf-8")

    assert calls == [
        "https://www.whitehallresources.com/sap-jobs/",
        "https://www.whitehallresources.com/job/junior-project-manager-pmo-analyst-32475/",
    ]
    assert result.detail_sample_url.endswith("/job/junior-project-manager-pmo-analyst-32475/")
    assert (result.artifact_dir / "detail-sample.html").exists()
    assert '"recipe_blueprint"' in report


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

    result = capture_recipe_calibration(
        "https://example.com/jobs",
        recipe_path=str(recipe_path),
        root=tmp_path,
        capture_detail=False,
    )

    assert calls == {"rendered": "https://example.com/jobs"}
    assert result.capture_mode == "rendered_html"


def _sthree_shell_html() -> str:
    return """
    <html><body>
      <div id="hitsList"></div>
      <script id="result-row" type="text/x-handlebars-template">
        <li class="job-search__item"><h2>{{title}}</h2><a href="/en-gb/job/{{slug}}/{{jobReference}}/"></a></li>
      </script>
      <script>
        var jobLanguage = 'en-gb';
        var initParams = {
          apiUrl:'https://api.globalenterprisepartners.example/',
          brandCode:'GEP'
        };
      </script>
    </body></html>
    """


def _sthree_payload(*, total: int, count: int) -> dict:
    records = [
        {
            "title": f"SAP Consultant {index}",
            "slug": f"sap-consultant-{index}",
            "jobReference": f"GEP-{index}",
            "location": "Remote",
            "remoteWorkingAvailable": True,
            "salaryText": "EUR 750/day",
            "jobType": "Contract",
            "postDate": "2026-06-09",
            "description": "<p>SAP contract delivery role with S/4HANA programme context.</p>",
        }
        for index in range(1, count + 1)
    ]
    return {"result": {"hits": total, "results": records}}


WHITEHALL_LIST_HTML = """
<!doctype html>
<html><body>
  <main>
    <h1>Available SAP Jobs</h1>
    <div class="job-item">
      <span>Job ID: BBBH66915_1779369758</span>
      <span class="job-type">Contract</span>
      <h3><a href="https://www.whitehallresources.com/job/junior-project-manager-pmo-analyst-32475/">Junior Project Manager / PMO Analyst</a></h3>
      <span class="job-location">Bonn, Nordrhein-Westfalen</span>
      <a class="button view" href="https://www.whitehallresources.com/job/junior-project-manager-pmo-analyst-32475/">View Job</a>
    </div>
    <div class="job-item">
      <span>Job ID: BBBH66903_1779291344</span>
      <span class="job-type">Contract</span>
      <h3><a href="https://www.whitehallresources.com/job/sap-wms-abap-technical-consultant-32335/">SAP WMS ABAP Technical Consultant</a></h3>
      <span class="job-location">United Arab Emirates</span>
      <a class="button view" href="https://www.whitehallresources.com/job/sap-wms-abap-technical-consultant-32335/">View Job</a>
    </div>
    <a class="page-numbers" href="https://www.whitehallresources.com/sap-jobs/page/2/">2</a>
    <a class="page-numbers" href="https://www.whitehallresources.com/sap-jobs/page/3/">3</a>
    <a class="page-numbers" href="https://www.whitehallresources.com/sap-jobs/page/4/">4</a>
    <a class="next page-numbers" href="https://www.whitehallresources.com/sap-jobs/page/2/">Next</a>
  </main>
</body></html>
"""


WHITEHALL_DETAIL_HTML = """
<!doctype html>
<html><body>
  <main>
    <section class="job-single">
      <h1>SAP EAM / PM Migration Consultant</h1>
      <div class="left-col">
        <div class="job-details">
          <span class="job-location">Sverige</span>
          <span class="job-type">Contract</span>
        </div>
      </div>
      <p>Whitehall Resources are currently looking for a SAP EAM / PM Migration Consultant on a remote basis.</p>
    </section>
    <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"JobPosting","title":"SAP EAM / PM Migration Consultant","description":"Whitehall Resources are currently looking for a SAP EAM / PM Migration Consultant with SAP PM and migration experience.","employmentType":"CONTRACT"}
    </script>
  </main>
</body></html>
"""
