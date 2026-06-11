from __future__ import annotations

import json
from pathlib import Path

from job_agent.cli import test_source as run_source_test_cli
from job_agent.io.json_store import write_json
from job_agent.models import Job, SourceRunResult, SourceWarning
from job_agent.services.source_session_service import SourceSessionService
from job_agent.services.source_test_service import SourceTestJobPreview, SourceTestResult, SourceTestService
from job_agent.store import JobStore


class FakeResponse:
    def __init__(self, text: str, url: str = "https://example.com/jobs") -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


def test_source_test_missing_source_returns_not_found(project_root: Path) -> None:
    result = SourceTestService(project_root).run_test("missing")

    assert result.status == "not_found"
    assert result.job_count == 0


def test_source_test_disabled_source_does_not_execute_adapter(monkeypatch, project_root: Path) -> None:
    _write_execution_source(project_root, enabled=False)

    def fail_if_called(source, root):
        raise AssertionError("adapter should not run")

    monkeypatch.setattr("job_agent.services.source_test_service.adapter_for_source", fail_if_called)

    result = SourceTestService(project_root).run_test("sample-source")

    assert result.status == "disabled"
    assert result.job_count == 0


def test_source_test_force_disabled_executes_adapter(monkeypatch, project_root: Path) -> None:
    _write_execution_source(project_root, enabled=False)

    class FakeAdapter:
        def fetch(self):
            return SourceRunResult(jobs=[Job(title="SAP ABAP Consultant", source="Sample", source_id="sample-source")])

    monkeypatch.setattr("job_agent.services.source_test_service.adapter_for_source", lambda source, root: FakeAdapter())

    result = SourceTestService(project_root).run_test("sample-source", force_disabled=True)

    assert result.status == "success"
    assert result.forced_disabled is True
    assert result.jobs[0].source_id == "sample-source"


def test_source_test_enabled_recipe_html_source_extracts_jobs(monkeypatch, project_root: Path) -> None:
    recipe_path = _write_recipe(project_root)
    _write_execution_source(project_root, enabled=True, recipe_path=recipe_path.relative_to(project_root).as_posix())
    html = """
    <article class="job-card">
      <a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a>
      <span class="location">Remote</span>
      <p class="description">ABAP RAP CDS contract role.</p>
    </article>
    """
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse(html))

    result = SourceTestService(project_root).run_test("sample-source")

    assert result.status == "success"
    assert result.job_count == 1
    assert result.jobs[0].title == "SAP ABAP Consultant"
    assert result.jobs[0].source_id == "sample-source"
    assert result.jobs[0].location == "Remote"


def test_source_test_indexes_all_listing_cards_without_recipe_card_cap(monkeypatch, project_root: Path) -> None:
    recipe_path = _write_recipe(project_root)
    recipe_path.write_text(
        "source_name: Test Recipe\n"
        "mode: static_html\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a.job-link\n"
        "  link_selector: a.job-link\n"
        "  description_selector: .description\n"
        "accept:\n"
        "  url_contains:\n"
        "    - /jobs/\n"
        "limits:\n"
        "  max_cards: 1\n",
        encoding="utf-8",
    )
    _write_execution_source(project_root, enabled=True, recipe_path=recipe_path.relative_to(project_root).as_posix())
    html = "\n".join(
        (
            "<article class='job-card'>"
            f"<a class='job-link' href='/jobs/sap-{index}'>SAP ABAP Consultant {index}</a>"
            "<p class='description'>ABAP RAP CDS contract role.</p>"
            "</article>"
        )
        for index in range(1, 4)
    )
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse(html))

    result = SourceTestService(project_root).run_test("sample-source")

    assert result.job_count == 3
    assert result.listing_limit_skipped_count == 0


def test_source_test_verifies_detail_reads_across_paginated_listing_pages(
    monkeypatch,
    project_root: Path,
) -> None:
    recipe_path = _write_recipe(project_root)
    recipe_path.write_text(
        "source_name: Test Recipe\n"
        "mode: static_html\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a.job-link\n"
        "  link_selector: a.job-link\n"
        "  description_selector: .description\n"
        "accept:\n"
        "  url_contains:\n"
        "    - /jobs/\n"
        "pagination:\n"
        "  page_link_selector: a.page-link\n"
        "  max_pages: 3\n"
        "  request_delay_seconds: 0\n"
        "detail:\n"
        "  follow: true\n"
        "  max_detail_pages: 99\n"
        "  description_selector: .detail-description\n",
        encoding="utf-8",
    )
    _write_execution_source(project_root, enabled=True, recipe_path=recipe_path.relative_to(project_root).as_posix())
    first_page_cards = "\n".join(
        (
            "<article class='job-card'>"
            f"<a class='job-link' href='/jobs/sap-{index}'>SAP Consultant {index}</a>"
            "<p class='description'>Listing summary.</p>"
            "</article>"
        )
        for index in range(1, 5)
    )
    second_page_cards = "\n".join(
        (
            "<article class='job-card'>"
            f"<a class='job-link' href='/jobs/sap-{index}'>SAP Consultant {index}</a>"
            "<p class='description'>Listing summary.</p>"
            "</article>"
        )
        for index in range(5, 9)
    )
    first_page_html = first_page_cards + "<a class='page-link' href='/jobs?page=2'>2</a>"
    second_page_html = second_page_cards
    fetched_urls = []

    def fake_get(url: str, *args, **kwargs):
        fetched_urls.append(url)
        if url == "https://example.com/jobs":
            return FakeResponse(first_page_html, url)
        if url == "https://example.com/jobs?page=2":
            return FakeResponse(second_page_html, url)
        return FakeResponse(
            "<main><section class='detail-description'>Verified detail text.</section></main>",
            url,
        )

    monkeypatch.setattr("requests.get", fake_get)

    result = SourceTestService(project_root).run_test("sample-source")

    checks = {check["capability"]: check for check in result.capability_checks}
    detail_urls = [url for url in fetched_urls if "/jobs/sap-" in url]
    assert result.job_count == 8
    assert result.pagination_fetch_count == 1
    assert result.detail_fetch_count == 2
    assert result.detail_enriched_count == 2
    assert result.detail_verified_listing_page_count == 2
    assert result.detail_listing_page_sample_target == 2
    assert detail_urls == ["https://example.com/jobs/sap-1", "https://example.com/jobs/sap-5"]
    assert checks["detail_navigation"]["status"] == "pass"
    assert "2/2 listing page" in checks["detail_navigation"]["detail"]
    assert result.log_dir
    artifact_dir = project_root / result.log_dir
    manifest = json.loads((project_root / result.log_manifest_path).read_text(encoding="utf-8"))
    entry_kinds = [entry["kind"] for entry in manifest["entries"]]
    assert artifact_dir.exists()
    assert "source_config" in entry_kinds
    assert "recipe" in entry_kinds
    assert "listing" in entry_kinds
    assert "pagination" in entry_kinds
    assert "detail" in entry_kinds
    assert (artifact_dir / "source-test-result.json").exists()
    assert (artifact_dir / "source-run-metadata.json").exists()
    assert any(
        "SAP Consultant 5" in (project_root / entry["html_path"]).read_text(encoding="utf-8")
        for entry in manifest["entries"]
        if entry["kind"] == "pagination"
    )


def test_source_test_reports_missing_session_for_session_required_recipe(project_root: Path) -> None:
    recipe_path = _write_recipe(project_root)
    recipe_path.write_text(
        "source_name: Test Recipe\n"
        "mode: static_html\n"
        "access:\n"
        "  requires_session: true\n"
        "  session_scope: example.com\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a.job-link\n"
        "  link_selector: a.job-link\n",
        encoding="utf-8",
    )
    _write_execution_source(project_root, enabled=True, recipe_path=recipe_path.relative_to(project_root).as_posix())

    result = SourceTestService(project_root).run_test("sample-source")

    checks = {check["capability"]: check for check in result.capability_checks}
    assert result.status == "failing"
    assert result.job_count == 0
    assert result.source_access_requires_session is True
    assert result.source_access_session_status == "missing"
    assert checks["source_access"]["status"] == "fail"
    assert "requires a connected session" in checks["source_access"]["detail"]


def test_source_test_uses_connected_session_for_session_required_recipe(monkeypatch, project_root: Path) -> None:
    recipe_path = _write_recipe(project_root)
    recipe_path.write_text(
        "source_name: Test Recipe\n"
        "mode: static_html\n"
        "access:\n"
        "  requires_session: true\n"
        "  session_scope: example.com\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a.job-link\n"
        "  link_selector: a.job-link\n",
        encoding="utf-8",
    )
    _write_execution_source(project_root, enabled=True, recipe_path=recipe_path.relative_to(project_root).as_posix())
    state_path = project_root / "sources" / "sessions" / "sample-source.storage-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"cookies": [{"name": "sid", "value": "abc", "domain": "example.com", "path": "/"}], "origins": []}',
        encoding="utf-8",
    )
    SourceSessionService(project_root).record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path=state_path.relative_to(project_root).as_posix(),
    )
    html = """
    <article class="job-card">
      <a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a>
    </article>
    """
    observed_cookie_names = []

    def fake_get(*args, **kwargs):
        observed_cookie_names.extend(cookie.name for cookie in kwargs.get("cookies", []))
        return FakeResponse(html)

    monkeypatch.setattr("requests.get", fake_get)

    result = SourceTestService(project_root).run_test("sample-source")

    checks = {check["capability"]: check for check in result.capability_checks}
    assert result.status == "success"
    assert result.job_count == 1
    assert result.source_access_session_status == "connected"
    assert checks["source_access"]["status"] == "pass"
    assert checks["source_access"]["observed"] is True
    assert "sid" in observed_cookie_names


def test_source_test_uses_connected_session_even_when_recipe_does_not_require_one(
    monkeypatch, project_root: Path
) -> None:
    recipe_path = _write_recipe(project_root)
    _write_execution_source(project_root, enabled=True, recipe_path=recipe_path.relative_to(project_root).as_posix())
    state_path = project_root / "sources" / "sessions" / "sample-source.storage-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"cookies": [{"name": "sid", "value": "abc", "domain": "example.com", "path": "/"}], "origins": []}',
        encoding="utf-8",
    )
    SourceSessionService(project_root).record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path=state_path.relative_to(project_root).as_posix(),
    )
    html = """
    <article class="job-card">
      <a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a>
    </article>
    """
    observed_cookie_names = []

    def fake_get(*args, **kwargs):
        observed_cookie_names.extend(cookie.name for cookie in kwargs.get("cookies", []))
        return FakeResponse(html)

    monkeypatch.setattr("requests.get", fake_get)

    result = SourceTestService(project_root).run_test("sample-source")

    assert result.status == "success"
    assert result.job_count == 1
    assert result.source_access_requires_session is False
    assert result.source_access_session_used is True
    assert result.source_access_session_status == "connected"
    assert "sid" in observed_cookie_names
    manifest_text = (project_root / result.log_manifest_path).read_text(encoding="utf-8")
    result_text = (project_root / result.log_dir / "source-test-result.json").read_text(encoding="utf-8")
    assert "abc" not in manifest_text
    assert "abc" not in result_text


def test_source_test_collects_warnings(monkeypatch, project_root: Path) -> None:
    _write_execution_source(project_root, enabled=True)

    class FakeAdapter:
        def fetch(self):
            return SourceRunResult(
                jobs=[Job(title="SAP ABAP Consultant", source="Sample", source_id="sample-source")],
                warnings=[SourceWarning("Sample", "Check details", "https://example.com/jobs")],
            )

    monkeypatch.setattr("job_agent.services.source_test_service.adapter_for_source", lambda source, root: FakeAdapter())

    result = SourceTestService(project_root).run_test("sample-source")

    assert result.status == "warning"
    assert result.warning_count == 1
    assert result.warnings == ["Sample: Check details"]


def test_source_test_explains_count_mismatch_and_seen_state(monkeypatch, project_root: Path) -> None:
    _write_execution_source(project_root, enabled=True)
    seen_job = Job(
        title="SAP ABAP Consultant",
        source="Sample",
        source_id="sample-source",
        url="https://example.com/jobs/sap-abap",
        description="ABAP RAP CDS contract role.",
    )
    new_job = Job(
        title="SAP Basis Consultant",
        source="Sample",
        source_id="sample-source",
        url="https://example.com/jobs/sap-basis",
        description="Basis operations role.",
    )
    write_json(
        project_root / "jobs" / "seen_jobs.json",
        [
            {
                "stable_id": JobStore.job_id(seen_job),
                "fuzzy_key": JobStore.fuzzy_key(seen_job),
                "title": seen_job.title,
                "company": seen_job.company,
                "source": seen_job.source,
                "url": seen_job.url,
                "first_seen_date": "2026-05-01",
                "last_seen_date": "2026-05-01",
                "content_hash": JobStore.content_hash(seen_job),
                "status": "previously_seen",
            }
        ],
    )

    class FakeAdapter:
        def fetch(self):
            return SourceRunResult(
                jobs=[seen_job, new_job],
                metadata={
                    "listing_observed_count": 3,
                    "listing_extracted_count": 2,
                    "listing_duplicate_count": 1,
                    "visible_total_job_count": 66,
                },
            )

    monkeypatch.setattr("job_agent.services.source_test_service.adapter_for_source", lambda source, root: FakeAdapter())

    result = SourceTestService(project_root).run_test("sample-source")

    assert result.listing_observed_count == 3
    assert result.listing_duplicate_count == 1
    assert result.visible_total_job_count == 66
    assert result.seen_new_count == 1
    assert result.seen_previously_seen_count == 1
    assert any("duplicate URL" in explanation for explanation in result.count_explanations)
    assert any("advertise 66" in explanation for explanation in result.count_explanations)
    assert any("already seen in previous runs" in explanation for explanation in result.count_explanations)


def test_source_test_does_not_write_packages_seen_state_or_runs(monkeypatch, project_root: Path) -> None:
    _write_execution_source(project_root, enabled=True)

    class FakeAdapter:
        def fetch(self):
            return SourceRunResult(jobs=[Job(title="SAP ABAP Consultant", source="Sample", source_id="sample-source")])

    monkeypatch.setattr("job_agent.services.source_test_service.adapter_for_source", lambda source, root: FakeAdapter())

    result = SourceTestService(project_root).run_test("sample-source")

    assert result.status == "success"
    assert not list((project_root / "output").glob("*/*/index.json"))
    assert not (project_root / "jobs" / "seen_jobs.json").exists()
    assert not (project_root / "output" / "runs" / "runs.json").exists()
    assert not list((project_root / "output" / "daily-digests").glob("*"))


def test_cli_source_test_prints_key_fields_and_no_writes(monkeypatch, capsys) -> None:
    class FakeService:
        def run_test(self, source_id, *, force_disabled=False):
            assert source_id == "sample-source"
            assert force_disabled is True
            return SourceTestResult(
                source_id="sample-source",
                source_name="Sample Recipe Source",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="success",
                job_count=1,
                jobs=[
                    SourceTestJobPreview(
                        title="SAP ABAP Consultant",
                        url="https://example.com/jobs/sap-abap",
                        source="Sample Recipe Source",
                        source_id="sample-source",
                        location="Remote",
                        description_preview="ABAP RAP CDS role.",
                    )
                ],
            )

    monkeypatch.setattr("job_agent.services.source_test_service.SourceTestService", lambda: FakeService())

    run_source_test_cli("sample-source", force_disabled=True)

    output = capsys.readouterr().out
    assert "Source id: sample-source" in output
    assert "Source test status: success" in output
    assert "SAP ABAP Consultant" in output
    assert "Source id: sample-source" in output
    assert "No packages, seen state, application materials, digests, or run records were written." in output


def _write_execution_source(
    project_root: Path,
    *,
    enabled: bool,
    recipe_path: str = "sources/recipes/test-recipe.yaml",
) -> None:
    (project_root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n"
        "  - name: Sample Recipe Source\n"
        "    source_id: sample-source\n"
        "    type: recipe_html\n"
        "    url: https://example.com/jobs\n"
        f"    recipe_path: {recipe_path}\n"
        f"    enabled: {'true' if enabled else 'false'}\n",
        encoding="utf-8",
    )


def _write_recipe(project_root: Path) -> Path:
    recipe_path = project_root / "sources" / "recipes" / "test-recipe.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(
        "source_name: Test Recipe\n"
        "mode: static_html\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a.job-link\n"
        "  link_selector: a.job-link\n"
        "  location_selector: .location\n"
        "  description_selector: .description\n"
        "accept:\n"
        "  url_contains:\n"
        "    - /jobs/\n",
        encoding="utf-8",
    )
    return recipe_path
