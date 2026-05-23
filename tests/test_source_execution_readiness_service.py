from __future__ import annotations

from pathlib import Path

from job_agent.cli import enable_source_when_ready, source_go_live_status, test_source as run_source_test_cli
from job_agent.io.yaml_store import read_yaml
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.recipe_preview_service import RecipePreviewResult
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_health_service import SourceHealthService
from job_agent.services.source_registry_service import SourceRegistryService
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


def test_go_live_status_reports_health_execution_path_and_readiness(project_root: Path) -> None:
    _prepare_good_source(project_root)

    readiness = SourceExecutionReadinessService(project_root).save_from_source_test(_source_test_result())

    assert readiness.checks["source_health_status"] == "good"
    assert readiness.checks["execution_entry_exists"] is True
    assert readiness.checks["execution_entry_recipe_path_matches_registry"] is True
    assert readiness.dry_run_status == "success"


def test_enablement_uses_registry_projection_without_manual_execution_entry(project_root: Path) -> None:
    _save_good_health(project_root)
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(_source_test_result())

    check = service.can_enable("eursap-jobs")

    assert check.can_enable is True
    assert check.readiness.checks["execution_entry_exists"] is True
    assert check.readiness.checks["execution_entry_recipe_path_matches_registry"] is True


def test_enablement_refuses_source_health_that_is_not_good(project_root: Path) -> None:
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=False)
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(_source_test_result())

    check = service.can_enable("eursap-jobs")

    assert check.can_enable is False
    assert any("Source health must be good" in blocker for blocker in check.blockers)


def test_enablement_uses_registry_recipe_path_over_stale_execution_entry(project_root: Path) -> None:
    _save_good_health(project_root)
    _write_mismatched_execution(project_root)
    service = SourceExecutionReadinessService(project_root)
    service.save_from_source_test(_source_test_result())

    check = service.can_enable("eursap-jobs")

    assert check.can_enable is True
    assert check.readiness.checks["execution_entry_recipe_path_matches_registry"] is True


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
    assert "No packages, seen state, materials, digests, or run records were written." in output


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
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    ExecutionSourceService(project_root).create_or_update_recipe_source(source, enabled=False)
    _save_good_health(project_root)


def _save_good_health(project_root: Path) -> None:
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


def _source_test_result(status: str = "success", job_count: int = 1, warnings: list[str] | None = None):
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
    )
