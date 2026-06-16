from __future__ import annotations

import os
import time
from pathlib import Path

from tests.helpers import seed_common_sources

from job_agent.cli import enable_source_when_ready, source_go_live_status
from job_agent.cli import test_source as run_source_test_cli
from job_agent.io.yaml_store import read_yaml
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.recipe_preview_service import RecipePreviewResult
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_health_service import SourceHealthService
from job_agent.services.source_listing_index_store import SourceListingIndexStore
from job_agent.services.source_registry_service import SourceRegistryService
from job_agent.services.source_session_service import SourceSessionService
from job_agent.services.source_test_service import SourceTestJobPreview, SourceTestResult


def test_successful_source_test_saves_ready_readiness_with_samples(project_root: Path) -> None:
    _prepare_good_source(project_root)

    readiness = SourceExecutionReadinessService(project_root).save_from_source_test(_source_test_result())

    assert readiness.readiness_status == "ready"
    assert readiness.dry_run_job_count == 1
    assert readiness.sample_titles == ["SAP Basis Consultant"]
    saved = read_yaml(project_root / "sources" / "source-execution-readiness.yaml", {})
    assert saved["sources"]["eursap-jobs"]["readiness_status"] == "ready"


def test_failing_or_zero_job_source_test_records_blocked(project_root: Path) -> None:
    _prepare_good_source(project_root)

    readiness = SourceExecutionReadinessService(project_root).save_from_source_test(
        _source_test_result(status="failing", job_count=0, warnings=["Adapter failed"])
    )

    assert readiness.readiness_status == "blocked"
    assert "Source test status is failing." in readiness.blockers
    assert "Source test extracted no jobs." in readiness.blockers


def test_pagination_capability_failure_blocks_readiness(project_root: Path) -> None:
    _prepare_good_source(project_root)

    readiness = SourceExecutionReadinessService(project_root).save_from_source_test(
        _source_test_result(
            capability_checks=[
                {
                    "capability": "pagination_navigation",
                    "status": "fail",
                    "detail": "Fetched 2 pagination page(s), but they produced only 0 new job(s).",
                },
                {
                    "capability": "pagination_duplicate_pages",
                    "status": "fail",
                    "detail": "2 fetched pagination page(s) returned only duplicate listings.",
                },
            ],
            pagination_duplicate_page_count=2,
            pagination_duplicate_ratio=1.0,
            pagination_unique_jobs_from_fetched_pages=0,
        )
    )

    assert readiness.readiness_status == "blocked"
    assert any("Pagination verification failed" in blocker for blocker in readiness.blockers)
    assert readiness.checks["pagination_duplicate_page_count"] == 2
    assert readiness.checks["pagination_duplicate_ratio"] == 1.0

    saved = read_yaml(project_root / "sources" / "source-execution-readiness.yaml", {})
    saved_record = saved["sources"]["eursap-jobs"]
    assert saved_record["dry_run_capability_checks"][0]["capability"] == "pagination_navigation"
    assert saved_record["dry_run_pagination_duplicate_page_count"] == 2
    assert saved_record["dry_run_pagination_duplicate_ratio"] == 1.0

    check = SourceExecutionReadinessService(project_root).can_enable("eursap-jobs")
    assert check.can_enable is False
    assert any("Pagination verification failed" in blocker for blocker in check.blockers)


def test_source_access_capability_failure_blocks_readiness(project_root: Path) -> None:
    _prepare_good_source(project_root)

    readiness = SourceExecutionReadinessService(project_root).save_from_source_test(
        _source_test_result(
            capability_checks=[
                {
                    "capability": "source_access",
                    "status": "fail",
                    "detail": "Recipe declares that this source requires a connected session.",
                }
            ],
        )
    )

    assert readiness.readiness_status == "blocked"
    assert any("Source access verification failed" in blocker for blocker in readiness.blockers)
    assert SourceExecutionReadinessService(project_root).can_enable("eursap-jobs").can_enable is False


def test_source_access_failure_is_primary_when_pagination_also_fails(project_root: Path) -> None:
    _prepare_good_source(project_root)

    readiness = SourceExecutionReadinessService(project_root).save_from_source_test(
        _source_test_result(
            capability_checks=[
                {
                    "capability": "pagination_strategy",
                    "status": "fail",
                    "detail": "Recipe declares url pagination, but proof-fetched pages returned only duplicate listings.",
                },
                {
                    "capability": "pagination_navigation",
                    "status": "fail",
                    "detail": "Fetched 2 pagination page(s), but later pages may require a logged-in session.",
                },
                {
                    "capability": "source_access",
                    "status": "fail",
                    "detail": "A connected source session was used, but the page still showed a sign-in gate.",
                },
            ],
            pagination_duplicate_page_count=1,
            pagination_duplicate_ratio=1.0,
        )
    )

    assert readiness.readiness_status == "blocked"
    assert readiness.blockers[0].startswith("Source access verification failed")
    assert "sign-in gate" in readiness.blockers[0]
    assert "Fetched 2 pagination" not in readiness.blockers[0]
    assert readiness.readiness_summary.startswith("Blocked: Source access verification failed")


def test_pagination_strategy_failure_blocks_readiness(project_root: Path) -> None:
    _prepare_good_source(project_root)

    readiness = SourceExecutionReadinessService(project_root).save_from_source_test(
        _source_test_result(
            capability_checks=[
                {
                    "capability": "pagination_strategy",
                    "status": "fail",
                    "detail": "Observed interactive pagination controls, but the recipe does not declare browser-click pagination.",
                }
            ],
        )
    )

    assert readiness.readiness_status == "blocked"
    assert any("Pagination strategy verification failed" in blocker for blocker in readiness.blockers)
    assert SourceExecutionReadinessService(project_root).can_enable("eursap-jobs").can_enable is False


def test_pagination_strategy_failure_is_prioritized_over_listing_coverage(project_root: Path) -> None:
    _prepare_good_source(project_root)

    readiness = SourceExecutionReadinessService(project_root).save_from_source_test(
        _source_test_result(
            capability_checks=[
                {
                    "capability": "listing_total_access",
                    "status": "fail",
                    "detail": "The listing page appears to advertise 75 postings, but the verified extractor reached only 22.",
                },
                {
                    "capability": "pagination_strategy",
                    "status": "fail",
                    "detail": "Recipe declares url pagination, but proof-fetched pages returned only duplicate listings.",
                },
            ],
        )
    )

    assert readiness.readiness_status == "blocked"
    assert readiness.blockers[0].startswith("Pagination strategy verification failed")


def test_visible_total_access_failure_blocks_readiness(project_root: Path) -> None:
    _prepare_good_source(project_root)

    readiness = SourceExecutionReadinessService(project_root).save_from_source_test(
        _source_test_result(
            capability_checks=[
                {
                    "capability": "listing_total_access",
                    "status": "fail",
                    "detail": "The listing page appears to advertise 66 postings, but the verified extractor reached only 23.",
                }
            ],
        )
    )

    assert readiness.readiness_status == "blocked"
    assert any("Listing coverage verification failed" in blocker for blocker in readiness.blockers)
    assert SourceExecutionReadinessService(project_root).can_enable("eursap-jobs").can_enable is False


def test_session_required_recipe_blocks_enablement_without_connected_session(project_root: Path) -> None:
    _prepare_good_source(project_root)
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(
        "source_name: Eursap Jobs\n"
        "mode: static_html\n"
        "access:\n"
        "  requires_session: true\n"
        "  session_scope: eursap.eu\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a\n"
        "  link_selector: a\n",
        encoding="utf-8",
    )
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(_source_test_result())

    check = service.can_enable("eursap-jobs")

    assert check.can_enable is False
    assert check.readiness.checks["source_session_required"] is True
    assert check.readiness.checks["source_session_status"] == "missing"
    assert any("Connected source session is required" in blocker for blocker in check.blockers)


def test_session_required_recipe_blocks_enablement_until_session_is_verified(project_root: Path) -> None:
    _prepare_session_required_source(project_root)
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    past_timestamp = time.time() - 5
    os.utime(recipe_path, (past_timestamp, past_timestamp))
    state_path = project_root / "sources" / "sessions" / "eursap-jobs.storage-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    session_service = SourceSessionService(project_root)
    session_service.record_storage_state(
        "eursap-jobs",
        session_scope="eursap.eu",
        storage_state_path=state_path.relative_to(project_root).as_posix(),
    )
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(
        _source_test_result(
            capability_checks=[
                {
                    "capability": "source_access",
                    "status": "pass",
                    "expected": True,
                    "observed": True,
                    "detail": "Connected source session was used for this verification run.",
                }
            ],
        )
    )

    unverified = service.can_enable("eursap-jobs")

    assert unverified.can_enable is False
    assert unverified.readiness.checks["source_session_usable"] is True
    assert unverified.readiness.checks["source_session_verified"] is False
    assert any("not verified" in blocker for blocker in unverified.blockers)

    session_service.mark_verified("eursap-jobs", session_scope="eursap.eu")
    verified = service.can_enable("eursap-jobs")

    assert verified.can_enable is True
    assert verified.readiness.checks["source_session_verified"] is True


def test_go_live_status_reports_health_execution_path_and_readiness(project_root: Path) -> None:
    _prepare_good_source(project_root)

    readiness = SourceExecutionReadinessService(project_root).save_from_source_test(_source_test_result())

    assert readiness.checks["source_health_status"] == "good"
    assert readiness.checks["execution_entry_exists"] is True
    assert readiness.checks["execution_entry_recipe_path_matches_registry"] is True
    assert readiness.dry_run_status == "success"


def test_enablement_requires_explicit_daily_run_projection(project_root: Path) -> None:
    _save_good_health(project_root)
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(_source_test_result())

    check = service.can_enable("eursap-jobs")

    assert check.can_enable is False
    assert check.readiness.checks["execution_entry_exists"] is False
    assert "No daily-run projection exists." in check.blockers


def test_enablement_uses_source_test_instead_of_legacy_preview_health(project_root: Path) -> None:
    seed_common_sources(project_root)
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=False)
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(_source_test_result())

    check = service.can_enable("eursap-jobs")

    assert check.can_enable is True
    assert check.readiness.checks["source_health_status"] == "untested"
    assert not any("Source health must be good" in blocker for blocker in check.blockers)


def test_enablement_refuses_stale_daily_run_projection(project_root: Path) -> None:
    _save_good_health(project_root)
    _write_mismatched_execution(project_root)
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(_source_test_result())

    check = service.can_enable("eursap-jobs")

    assert check.can_enable is False
    assert check.readiness.checks["execution_entry_recipe_path_matches_registry"] is False
    assert "Daily-run projection recipe_path does not match source registry recipe_path." in check.blockers


def test_enablement_refuses_without_saved_source_test_readiness(project_root: Path) -> None:
    _prepare_good_source(project_root)

    check = SourceExecutionReadinessService(project_root).can_enable("eursap-jobs")

    assert check.can_enable is False
    assert "No saved source test readiness result." in check.blockers


def test_enablement_refuses_blocked_readiness(project_root: Path) -> None:
    _prepare_good_source(project_root)
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(_source_test_result(status="success", job_count=0))

    check = service.can_enable("eursap-jobs")

    assert check.can_enable is False
    assert "Source test extracted no jobs." in check.blockers


def test_enablement_refuses_readiness_saved_before_recipe_change(project_root: Path) -> None:
    _prepare_good_source(project_root)
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(_source_test_result())
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text("source_name: Eursap Jobs\nlisting: {}\n", encoding="utf-8")
    future_timestamp = time.time() + 60
    os.utime(recipe_path, (future_timestamp, future_timestamp))

    check = service.can_enable("eursap-jobs")

    assert check.can_enable is False
    assert check.readiness.checks["recipe_changed_after_source_test"] is True
    assert "Reading plan changed since the saved source test; rerun the safe source test." in check.blockers


def test_recipe_change_makes_old_pagination_failure_historical(project_root: Path) -> None:
    _prepare_good_source(project_root)
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(
        _source_test_result(
            capability_checks=[
                {
                    "capability": "pagination_strategy",
                    "status": "fail",
                    "detail": "URL pagination returned only duplicate listings.",
                }
            ],
            pagination_duplicate_page_count=2,
            pagination_duplicate_ratio=1.0,
        )
    )
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text("source_name: Eursap Jobs\nlisting: {}\n", encoding="utf-8")
    future_timestamp = time.time() + 60
    os.utime(recipe_path, (future_timestamp, future_timestamp))

    readiness = service.evaluate("eursap-jobs")

    assert readiness.readiness_status == "blocked"
    assert "Reading plan changed since the saved source test; rerun the safe source test." in readiness.blockers
    assert not any("Pagination" in blocker for blocker in readiness.blockers)


def test_enable_when_ready_sets_execution_enabled_without_running_source(project_root: Path) -> None:
    _prepare_good_source(project_root)
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(_source_test_result())

    result = service.enable_when_ready("eursap-jobs")

    config = read_yaml(project_root / "sources" / "recruiting-sites.yaml", {})
    assert result.enabled is True
    assert config["sources"][0]["enabled"] is True
    assert not list((project_root / "output").glob("*/*/index.json"))
    assert not (project_root / "jobs" / "seen_jobs.json").exists()


def test_cli_source_test_save_readiness_prints_saved_summary(monkeypatch, capsys, project_root: Path) -> None:
    _prepare_good_source(project_root)

    class FakeSourceTestService:
        def __init__(self, root):
            pass

        def run_test(self, source_id, *, force_disabled=False):
            assert source_id == "eursap-jobs"
            assert force_disabled is True
            return _source_test_result()

    monkeypatch.setattr("job_agent.services.source_test_service.SourceTestService", FakeSourceTestService)

    run_source_test_cli("eursap-jobs", force_disabled=True, save_readiness=True, root=project_root)

    output = capsys.readouterr().out
    assert "Readiness saved: ready" in output
    assert "Listing index refreshed: 1 listings" in output
    assert "No packages, seen state, application materials, digests, or run records were written." in output
    assert SourceListingIndexStore(project_root).summary_for_source("eursap-jobs").indexed_count == 1
    assert not (project_root / "jobs" / "seen_jobs.json").exists()


def test_cli_go_live_status_and_enable_when_ready(capsys, project_root: Path) -> None:
    _prepare_good_source(project_root)
    SourceExecutionReadinessService(project_root).save_from_source_test(_source_test_result())

    source_go_live_status("eursap-jobs", root=project_root)
    status_output = capsys.readouterr().out
    assert "Readiness status: ready" in status_output
    assert "Daily-run projection present: True" in status_output

    enable_source_when_ready("eursap-jobs", root=project_root)
    enable_output = capsys.readouterr().out
    assert "Source enabled: eursap-jobs" in enable_output
    assert "No source run or daily run was started." in enable_output


def _prepare_good_source(project_root: Path) -> None:
    seed_common_sources(project_root)
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=False)
    _save_good_health(project_root)


def _prepare_session_required_source(project_root: Path) -> None:
    _prepare_good_source(project_root)
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(
        "source_name: Eursap Jobs\n"
        "mode: static_html\n"
        "access:\n"
        "  requires_session: true\n"
        "  session_scope: eursap.eu\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a\n"
        "  link_selector: a\n",
        encoding="utf-8",
    )


def _save_good_health(project_root: Path) -> None:
    seed_common_sources(project_root)
    SourceHealthService(project_root).save_preview(
        "eursap-jobs",
        RecipePreviewResult(
            recipe_source_name="Eursap Jobs (experimental)",
            recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
            recipe_status="experimental",
            input_type="local artifact",
            input_value="output/recipe-calibration/eursap/page.html",
            base_url="https://eursap.eu/jobs",
            mode_used="local_fixture_html",
            extracted_job_count=1,
            useful_titles=1,
            generic_labels=0,
            unique_urls=1,
            average_description_length=120,
            jobs=[],
            warnings=[],
        ),
    )


def _write_mismatched_execution(project_root: Path) -> None:
    (project_root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n"
        "  - name: Eursap Jobs\n"
        "    source_id: eursap-jobs\n"
        "    type: recipe_html\n"
        "    url: https://eursap.eu/jobs\n"
        "    recipe_path: sources/recipes/experimental/old.yaml\n"
        "    enabled: false\n",
        encoding="utf-8",
    )


def _source_test_result(
    status: str = "success",
    job_count: int = 1,
    warnings: list[str] | None = None,
    capability_checks: list[dict] | None = None,
    pagination_duplicate_page_count: int = 0,
    pagination_duplicate_ratio: float = 0.0,
    pagination_unique_jobs_from_fetched_pages: int = 0,
):
    jobs = []
    if job_count:
        jobs = [
            SourceTestJobPreview(
                title="SAP Basis Consultant",
                url="https://eursap.eu/jobs/sap-basis",
                source="Eursap Jobs",
                source_id="eursap-jobs",
            )
        ]
    return SourceTestResult(
        source_id="eursap-jobs",
        source_name="Eursap Jobs",
        source_type="recipe_html",
        source_enabled=False,
        forced_disabled=True,
        status=status,
        job_count=job_count,
        warning_count=len(warnings or []),
        warnings=warnings or [],
        jobs=jobs,
        capability_checks=capability_checks or [],
        pagination_duplicate_page_count=pagination_duplicate_page_count,
        pagination_duplicate_ratio=pagination_duplicate_ratio,
        pagination_unique_jobs_from_fetched_pages=pagination_unique_jobs_from_fetched_pages,
    )
