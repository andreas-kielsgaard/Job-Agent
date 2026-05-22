from __future__ import annotations

from pathlib import Path

from job_agent.services.recipe_preview_service import PreviewJob, RecipePreviewResult, preview_recipe
from job_agent.services.source_health_service import SourceHealthService, derive_health_status


def _preview(
    extracted_job_count: int = 2,
    useful_titles: int = 2,
    generic_labels: int = 0,
    unique_urls: int = 2,
    warnings: list[str] | None = None,
) -> RecipePreviewResult:
    return RecipePreviewResult(
        recipe_source_name="Eursap Jobs (experimental)",
        recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
        recipe_status="experimental",
        input_type="local artifact",
        input_value="output/recipe-calibration/page.html",
        base_url="https://eursap.eu/jobs",
        mode_used="local_fixture_html",
        extracted_job_count=extracted_job_count,
        useful_titles=useful_titles,
        generic_labels=generic_labels,
        unique_urls=unique_urls,
        average_description_length=160,
        jobs=[
            PreviewJob(
                title="SAP Basis Consultant",
                url="https://eursap.eu/jobs/sap-basis-consultant-34235-remote",
                location="Remote Work",
                remote="Not listed",
                rate="Market Rate",
                workload="Contract",
                posted_date="Not listed",
                start_date="Sep 01, 2026",
                languages=["English"],
                description_preview="SAP Basis preview.",
                extraction_notes=["Recipe extracted job ID: 34235"],
            )
        ],
        warnings=warnings or [],
    )


def test_source_health_file_load_save_and_get(project_root: Path) -> None:
    service = SourceHealthService(project_root)

    record = service.save_preview("eursap-jobs", _preview())

    assert (project_root / "sources" / "source-health.yaml").exists()
    assert record.health_status == "good"
    loaded = service.get_health("eursap-jobs")
    assert loaded.extracted_job_count == 2
    assert loaded.useful_titles == 2
    assert loaded.last_input_type == "local artifact"
    assert loaded.health_summary == "2 jobs extracted, 2 useful titles, no generic labels."


def test_source_health_status_rules() -> None:
    assert derive_health_status(0, 0, 0, 0, []) == "failing"
    assert derive_health_status(2, 2, 0, 2, []) == "good"
    assert derive_health_status(2, 2, 1, 2, []) == "warning"
    assert derive_health_status(2, 2, 0, 2, ["Local fixture warning"]) == "warning"
    assert derive_health_status(2, 2, 0, 1, []) == "warning"


def test_missing_health_record_is_untested(project_root: Path) -> None:
    record = SourceHealthService(project_root).get_health("eursap-jobs")

    assert record.health_status == "untested"
    assert record.health_summary == "No preview has been saved for this source yet."


def test_preview_without_source_id_does_not_write_health(project_root: Path) -> None:
    preview = preview_recipe(
        "tests/fixtures/recipes/experimental/eursap-jobs.yaml",
        "tests/fixtures/real_sources/eursap-jobs.html",
        base_url="https://eursap.eu/jobs",
    )

    assert preview.extracted_job_count == 2
    assert not (project_root / "sources" / "source-health.yaml").exists()


def test_saving_preview_result_updates_source_health(project_root: Path) -> None:
    preview = _preview(warnings=["Local fixture HTML ignores recipe mode: rendered_html."])

    record = SourceHealthService(project_root).save_preview("montreal-associates-jobs", preview)

    assert record.health_status == "warning"
    assert record.warnings == ["Local fixture HTML ignores recipe mode: rendered_html."]
    assert SourceHealthService(project_root).get_health("montreal-associates-jobs").warnings_count == 1


def test_saving_failure_records_failing_health(project_root: Path) -> None:
    record = SourceHealthService(project_root).save_failure(
        "eursap-jobs",
        "missing.html",
        "unknown",
        "HTML fixture not found: missing.html",
    )

    assert record.health_status == "failing"
    assert record.extracted_job_count == 0
    assert record.warnings == ["HTML fixture not found: missing.html"]
    assert "Preview failed" in SourceHealthService(project_root).get_health("eursap-jobs").health_summary
