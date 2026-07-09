from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.models import Job, SourceRunResult, SourceWarning
from job_agent.services.source_listing_index_service import SourceListingIndexService
from job_agent.services.source_listing_index_store import SourceListingIndexStore
from job_agent.store import JobStore


def test_source_listing_index_counts_reviewed_without_fetching_details(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    _write_recipe_source(project_root)

    seen_job = Job(
        title="SAP ABAP Consultant 1",
        source="Detail Source",
        source_id="detail-source",
        url="https://example.com/jobs/job-1",
        description="Detailed role already reviewed.",
    )
    JobStore(project_root).mark_seen([seen_job])
    detail_calls = []

    def fake_fetch_static(url: str, timeout_seconds: int):
        return _listing_html(), url, []

    def fake_detail_get(*args, **kwargs):
        detail_calls.append(args[0])
        raise AssertionError("Listing indexing should not fetch detail pages")

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)
    monkeypatch.setattr("requests.get", fake_detail_get)
    progress_events = []

    result = SourceListingIndexService(project_root).index("detail-source", progress_callback=progress_events.append)

    assert result.status == "completed"
    assert result.job_count == 3
    assert result.reviewed_in_detail_count == 1
    assert result.waiting_for_detail_count == 2
    assert "3 jobs indexed, 1 reviewed in detail, 2 waiting" in result.summary
    summary = SourceListingIndexStore(project_root).summary_for_source("detail-source")
    assert summary.indexed_count == 3
    assert summary.status_label == "Indexed"
    assert detail_calls == []
    assert any(event.get("page_explored_count") == 1 for event in progress_events)
    assert any(event.get("jobs_found") == 3 for event in progress_events)


def test_source_listing_index_marks_missing_seen_jobs_no_longer_posted(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    _write_recipe_source(project_root)
    active_seen = Job(
        title="SAP ABAP Consultant 1",
        source="Detail Source",
        source_id="detail-source",
        url="https://example.com/jobs/job-1",
        description="Reviewed and still listed.",
    )
    stale_seen = Job(
        title="SAP ABAP Consultant Removed",
        source="Detail Source",
        source_id="detail-source",
        url="https://example.com/jobs/removed",
        description="Reviewed earlier, now gone.",
    )
    JobStore(project_root).mark_seen([active_seen, stale_seen])

    def fake_fetch_static(url: str, timeout_seconds: int):
        return _listing_html(), url, []

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)

    result = SourceListingIndexService(project_root).index("detail-source")

    records = {record.url: record for record in JobStore(project_root, create=False).list_seen_records()}
    assert result.no_longer_posted_count == 1
    assert "1 historical posting no longer posted" in result.summary
    assert records["https://example.com/jobs/job-1"].posting_status == "active"
    assert records["https://example.com/jobs/removed"].posting_status == "no_longer_posted"


def test_source_listing_index_result_reports_duplicate_pagination_pages(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    _write_recipe_source(project_root)

    class FakeAdapter:
        def fetch(self, progress_callback=None, options=None):
            return SourceRunResult(
                jobs=[
                    Job(
                        title="SAP ABAP Consultant 1",
                        source="Detail Source",
                        source_id="detail-source",
                        url="https://example.com/jobs/job-1",
                    )
                ],
                warnings=[
                    SourceWarning(
                        "Detail Source",
                        "Pagination pages returned only listings already seen on earlier pages.",
                    )
                ],
                metadata={
                    "pagination_strategy": "url",
                    "pagination_fetch_count": 6,
                    "pagination_max_pages": 7,
                    "pagination_duplicate_page_count": 6,
                    "pagination_duplicate_ratio": 1.0,
                },
            )

    monkeypatch.setattr(
        "job_agent.services.source_listing_index_service.adapter_for_source", lambda source, root: FakeAdapter()
    )

    result = SourceListingIndexService(project_root).index("detail-source")

    assert result.status == "completed_with_warnings"
    assert result.page_explored_count == 7
    assert result.page_total == 7
    assert result.pagination_duplicate_page_count == 6
    assert "6 pagination pages returned duplicate or inaccessible listings" in result.summary


def test_source_listing_index_does_not_mark_jobs_stale_when_readiness_is_blocked(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    _write_recipe_source(project_root)
    _write_blocked_readiness(project_root)
    seen_job = Job(
        title="SAP ABAP Consultant Removed",
        source="Detail Source",
        source_id="detail-source",
        url="https://example.com/jobs/removed",
        description="Previously reviewed posting.",
    )
    JobStore(project_root).mark_seen([seen_job])
    fetch_calls = []

    def fake_fetch_static(url: str, timeout_seconds: int):
        fetch_calls.append(url)
        return _listing_html(), url, []

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)

    result = SourceListingIndexService(project_root).index("detail-source")

    records = {record.url: record for record in JobStore(project_root, create=False).list_seen_records()}
    assert result.status == "failed"
    assert "Saved source readiness is blocked" in result.summary
    assert fetch_calls == []
    assert records["https://example.com/jobs/removed"].posting_status != "no_longer_posted"
    assert SourceListingIndexStore(project_root).summary_for_source("detail-source").indexed_count == 0


def test_source_listing_index_blocks_required_missing_session_before_fetch(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    _write_recipe_source(project_root)
    recipe_path = project_root / "sources" / "recipes" / "detail-source.yaml"
    recipe_path.write_text(
        "source_name: Detail Source\n"
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
    fetch_calls = []

    def fake_fetch_static(url: str, timeout_seconds: int):
        fetch_calls.append(url)
        return _listing_html(), url, []

    monkeypatch.setattr("job_agent.services.job_board_recipe_service._fetch_static_html", fake_fetch_static)

    result = SourceListingIndexService(project_root).index("detail-source")

    assert result.status == "failed"
    assert "requires a connected session" in result.summary
    assert fetch_calls == []
    assert SourceListingIndexStore(project_root).summary_for_source("detail-source").indexed_count == 0


def _write_recipe_source(root: Path) -> None:
    recipe_path = root / "sources" / "recipes" / "detail-source.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(
        "source_name: Detail Source\n"
        "mode: static_html\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a.job-link\n"
        "  link_selector: a.job-link\n"
        "  description_selector: .summary\n"
        "detail:\n"
        "  follow: true\n"
        "  description_selector: .detail\n"
        "accept:\n"
        "  url_contains:\n"
        "    - /jobs/\n"
        "limits:\n"
        "  max_cards: 1\n",
        encoding="utf-8",
    )
    (root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n"
        "  - name: Detail Source\n"
        "    source_id: detail-source\n"
        "    type: recipe_html\n"
        "    url: https://example.com/jobs\n"
        f"    recipe_path: {recipe_path.relative_to(root).as_posix()}\n"
        "    enabled: false\n",
        encoding="utf-8",
    )


def _write_blocked_readiness(root: Path) -> None:
    (root / "sources" / "source-registry.yaml").write_text(
        "sources:\n"
        "  - id: detail-source\n"
        "    name: Detail Source\n"
        "    kind: recipe\n"
        "    status: testing\n"
        "    url: https://example.com/jobs\n"
        "    recipe_path: sources/recipes/detail-source.yaml\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    (root / "sources" / "source-health.yaml").write_text(
        "sources:\n"
        "  detail-source:\n"
        "    last_preview_at: '2026-06-04T00:00:00+00:00'\n"
        "    extracted_job_count: 3\n"
        "    useful_titles: 3\n"
        "    unique_urls: 3\n"
        "    health_status: good\n"
        "    health_summary: 3 jobs extracted, 3 useful titles, no generic labels.\n",
        encoding="utf-8",
    )
    (root / "sources" / "source-execution-readiness.yaml").write_text(
        "sources:\n"
        "  detail-source:\n"
        "    last_checked_at: '2999-01-01T00:00:00+00:00'\n"
        "    dry_run_status: warning\n"
        "    dry_run_job_count: 3\n"
        "    dry_run_warning_count: 1\n"
        "    dry_run_warnings: []\n"
        "    dry_run_capability_checks:\n"
        "      - capability: pagination_navigation\n"
        "        status: fail\n"
        "        detail: Later listing pages require a verified source session.\n"
        "    dry_run_pagination_duplicate_page_count: 1\n"
        "    dry_run_pagination_duplicate_ratio: 1.0\n"
        "    readiness_status: blocked\n"
        "    readiness_summary: Blocked.\n"
        "    checks: {}\n"
        "    blockers:\n"
        "      - Pagination verification failed: Later listing pages require a verified source session.\n"
        "    warnings: []\n",
        encoding="utf-8",
    )


def _listing_html() -> str:
    return "\n".join(
        (
            "<article class='job-card'>"
            f"<a class='job-link' href='/jobs/job-{index}'>SAP ABAP Consultant {index}</a>"
            "<p class='summary'>SAP contract role.</p>"
            "</article>"
        )
        for index in range(1, 4)
    )
