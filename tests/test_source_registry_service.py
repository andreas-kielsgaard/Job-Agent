from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.helpers import (
    EURSAP_SOURCE,
    MANUAL_SOURCE,
    SAMPLE_SOURCE,
    seed_common_sources,
    seed_source_registry,
)

from job_agent.io.yaml_store import read_yaml
from job_agent.services.source_registry_service import SourceRegistryService


def test_default_source_registry_creation_and_listing(project_root: Path) -> None:
    service = SourceRegistryService(project_root)

    sources = service.list_sources()

    assert (project_root / "sources" / "source-registry.yaml").exists()
    assert sources == []


def test_existing_partial_registry_is_not_augmented_with_starter_sources(project_root: Path) -> None:
    registry = project_root / "sources" / "source-registry.yaml"
    registry.write_text(
        "sources:\n  - id: manual-intake\n    name: Manual Intake\n    kind: manual\n    status: active\n",
        encoding="utf-8",
    )

    sources = SourceRegistryService(project_root).list_sources()

    ids = {source.id for source in sources}
    assert ids == {"manual-intake"}


def test_registry_does_not_discover_recipe_files_as_sources(project_root: Path) -> None:
    recipe = project_root / "sources" / "recipes" / "experimental" / "acme-jobs.yaml"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text(
        "source_name: Acme Jobs\n"
        "start_url: https://example.com/jobs\n"
        "listing:\n"
        "  card_selector: article\n"
        "  title_selector: h2\n"
        "  link_selector: a\n",
        encoding="utf-8",
    )

    source = SourceRegistryService(project_root).get_source("acme-jobs")

    assert source is None


def test_source_registry_normalizes_missing_and_invalid_fields(project_root: Path) -> None:
    registry = project_root / "sources" / "source-registry.yaml"
    registry.write_text(
        "sources:\n  - name: Strange Source\n    kind: mystery\n    status: weird\n    tags: sap\n",
        encoding="utf-8",
    )

    source = SourceRegistryService(project_root).list_sources()[0]

    assert source.id == "strange-source"
    assert source.kind == "manual"
    assert source.status == "needs_review"
    assert source.tags == ["sap"]
    assert source.recipe_state == "none"


def test_get_source_by_id_and_recipe_status(project_root: Path) -> None:
    seed_common_sources(project_root)
    service = SourceRegistryService(project_root)

    source = service.get_source("eursap-jobs")

    assert source is not None
    assert source.recipe_path == "sources/recipes/experimental/eursap-jobs.yaml"
    assert source.recipe_state == "live-calibrated experimental"
    assert source.enabled is False


def test_update_source_persists_default_source_edits(project_root: Path) -> None:
    seed_common_sources(project_root)
    service = SourceRegistryService(project_root)

    updated = service.update_source(
        "eursap-jobs",
        name="Eursap Jobs Reviewed",
        kind="recipe",
        url="https://eursap.eu/jobs?contract=sap",
        status="ready",
        recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
        notes="Reviewed for daily-run prep.",
    )

    registry = read_yaml(project_root / "sources" / "source-registry.yaml", {})
    saved = next(item for item in registry["sources"] if item["id"] == "eursap-jobs")
    assert updated.name == "Eursap Jobs Reviewed"
    assert updated.kind == "recipe"
    assert updated.status == "ready"
    assert saved["name"] == "Eursap Jobs Reviewed"
    assert saved["kind"] == "recipe"
    assert saved["status"] == "ready"
    assert saved["url"] == "https://eursap.eu/jobs?contract=sap"
    assert saved["notes"] == "Reviewed for daily-run prep."


def test_add_source_saves_review_entry_without_daily_run_execution(project_root: Path) -> None:
    service = SourceRegistryService(project_root)

    created = service.add_source(
        name="Accuro Projects",
        url="https://www.accuro.dk/freelance-projects",
        notes="Interesting Nordic contract source.",
    )

    registry = read_yaml(project_root / "sources" / "source-registry.yaml", {})
    saved = next(item for item in registry["sources"] if item["id"] == "accuro-projects")
    assert created.id == "accuro-projects"
    assert created.kind == "job_board"
    assert created.status == "needs_review"
    assert created.enabled is False
    assert saved["url"] == "https://www.accuro.dk/freelance-projects"
    assert saved["recipe_path"] == ""
    assert saved["notes"] == "Interesting Nordic contract source."


def test_add_source_can_start_with_existing_recipe(project_root: Path) -> None:
    recipe = project_root / "sources" / "recipes" / "experimental" / "accuro.yaml"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text(
        "source_name: Accuro\nlisting:\n  card_selector: article\n  title_selector: h2\n  link_selector: a\n",
        encoding="utf-8",
    )

    created = SourceRegistryService(project_root).add_source(
        name="Accuro",
        url="https://www.accuro.dk/freelance-projects",
        recipe_path="sources/recipes/experimental/accuro.yaml",
    )

    assert created.kind == "recipe"
    assert created.status == "testing"
    assert created.recipe_path == "sources/recipes/experimental/accuro.yaml"
    assert "recipe" in created.tags


def test_add_source_rejects_duplicate_url(project_root: Path) -> None:
    seed_source_registry(project_root, EURSAP_SOURCE)
    service = SourceRegistryService(project_root)

    with pytest.raises(ValueError, match="Source already exists"):
        service.add_source(name="Duplicate", url="https://eursap.eu/jobs")


def test_update_source_rejects_unknown_status(project_root: Path) -> None:
    seed_common_sources(project_root)
    with pytest.raises(ValueError, match="Unsupported source status"):
        SourceRegistryService(project_root).update_source(
            "eursap-jobs",
            name="Eursap Jobs",
            kind="recipe",
            url="https://eursap.eu/jobs",
            status="live",
            recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
            notes="",
        )


def test_archive_and_restore_source_hide_without_deleting(project_root: Path) -> None:
    seed_common_sources(project_root)
    service = SourceRegistryService(project_root)

    archived = service.archive_source("whitehall-sap-contract")

    registry = read_yaml(project_root / "sources" / "source-registry.yaml", {})
    saved = next(item for item in registry["sources"] if item["id"] == "whitehall-sap-contract")
    assert archived.status == "archived"
    assert saved["status"] == "archived"
    assert saved["enabled"] is False
    assert "archived_at" in saved

    restored = service.restore_source("whitehall-sap-contract")

    assert restored.status == "needs_review"
    assert restored.kind == "recipe"


def test_registry_includes_saved_source_health(project_root: Path) -> None:
    from job_agent.services.recipe_preview_service import RecipePreviewResult
    from job_agent.services.source_health_service import SourceHealthService

    seed_common_sources(project_root)

    preview = RecipePreviewResult(
        recipe_source_name="Eursap Jobs (experimental)",
        recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
        recipe_status="experimental",
        input_type="local artifact",
        input_value="artifact/page.html",
        base_url="https://eursap.eu/jobs",
        mode_used="local_fixture_html",
        extracted_job_count=9,
        useful_titles=9,
        generic_labels=0,
        unique_urls=9,
        average_description_length=177,
        jobs=[],
        warnings=[],
    )
    SourceHealthService(project_root).save_preview("eursap-jobs", preview)

    source = SourceRegistryService(project_root).get_source("eursap-jobs")

    assert source is not None
    assert source.health.health_status == "good"
    assert source.health.extracted_job_count == 9


def test_registry_loading_does_not_change_daily_run_source_config(project_root: Path) -> None:
    source_config = project_root / "sources" / "recruiting-sites.yaml"
    source_config.write_text(
        "sources:\n  - name: Local Sample\n    type: local_yaml\n    path: jobs/raw/sample_jobs.yaml\n",
        encoding="utf-8",
    )
    before = source_config.read_text(encoding="utf-8")

    SourceRegistryService(project_root).list_sources()

    assert source_config.read_text(encoding="utf-8") == before


def test_source_stats_with_no_packages_show_no_data(project_root: Path) -> None:
    seed_common_sources(project_root)
    source = SourceRegistryService(project_root).get_source("eursap-jobs")

    assert source is not None
    assert source.stats.jobs_found_total == 0
    assert source.stats.value_status == "no_data"
    assert source.stats.value_label == "No saved jobs yet"
    assert source.stats.value_summary == "No saved jobs from this source yet."


def test_source_stats_with_strong_and_exploratory_packages_are_promising(project_root: Path) -> None:
    seed_source_registry(project_root, SAMPLE_SOURCE)
    _write_package(
        project_root,
        "run-20260509",
        "p1",
        {
            "stable_id": "job-1",
            "run_id": "run-20260509",
            "title": "SAP ABAP Consultant",
            "source": "Sample Jobs",
            "source_url": "https://example.com/job-1",
            "match_score": 88,
            "match_category": "strong",
            "application_status": "applied",
        },
    )
    _write_package(
        project_root,
        "run-20260510",
        "p2",
        {
            "stable_id": "job-2",
            "run_id": "run-20260510",
            "title": "SAP Integration Consultant",
            "source": "Sample Jobs",
            "source_url": "https://example.com/job-2",
            "match_score": 72,
            "match_category": "exploratory",
            "application_status": "unreviewed",
        },
    )

    source = SourceRegistryService(project_root).get_source("sample-jobs")

    assert source is not None
    assert source.stats.jobs_found_total == 2
    assert source.stats.strong_matches == 1
    assert source.stats.exploratory_matches == 1
    assert source.stats.applied_count == 1
    assert source.stats.unreviewed_count == 1
    assert source.stats.average_match_score == 80
    assert source.stats.best_match_score == 88
    assert source.stats.best_recent_match_title == "SAP ABAP Consultant"
    assert source.stats.value_status == "promising"
    assert source.stats.value_label == "Promising results"


def test_source_stats_with_not_interesting_low_score_packages_are_low_value(project_root: Path) -> None:
    seed_source_registry(project_root, SAMPLE_SOURCE)
    _write_package(
        project_root,
        "run-20260509",
        "p1",
        {
            "stable_id": "job-1",
            "run_id": "run-20260509",
            "title": "Unrelated Analyst",
            "source": "Sample Jobs",
            "source_url": "https://example.com/job-1",
            "match_score": 20,
            "match_category": "weak",
            "application_status": "not_interesting",
        },
    )
    _write_package(
        project_root,
        "run-20260509",
        "p2",
        {
            "stable_id": "job-2",
            "run_id": "run-20260509",
            "title": "Excluded Manager",
            "source": "Sample Jobs",
            "source_url": "https://example.com/job-2",
            "match_score": 12,
            "match_category": "excluded",
            "application_status": "not_interesting",
        },
    )

    source = SourceRegistryService(project_root).get_source("sample-jobs")

    assert source is not None
    assert source.stats.jobs_found_total == 2
    assert source.stats.weak_or_excluded_matches == 2
    assert source.stats.not_interesting_count == 2
    assert source.stats.value_status == "low_value"
    assert source.stats.value_label == "Mostly low fit"


def test_manual_intake_matches_manual_posting_packages(project_root: Path) -> None:
    seed_source_registry(project_root, MANUAL_SOURCE)
    _write_package(
        project_root,
        "manual-20260509",
        "p1",
        {
            "stable_id": "manual-1",
            "run_id": "manual-20260509",
            "title": "Manual SAP Contract",
            "source": "Recruiter Mail",
            "source_url": "",
            "match_score": 66,
            "match_category": "exploratory",
            "application_status": "interesting",
        },
    )

    source = SourceRegistryService(project_root).get_source("manual-intake")

    assert source is not None
    assert source.stats.interesting_count == 1
    assert source.stats.jobs_found_total == 1
    assert source.stats.exploratory_matches == 1
    assert source.stats.value_status == "promising"


def test_source_url_matching_uses_matching_domain_and_path(project_root: Path) -> None:
    seed_source_registry(project_root, EURSAP_SOURCE)
    _write_package(
        project_root,
        "run-20260509",
        "match",
        {
            "stable_id": "eursap-1",
            "run_id": "run-20260509",
            "title": "SAP Basis Consultant",
            "source": "Other Label",
            "source_url": "https://eursap.eu/jobs/sap-basis-consultant",
            "match_score": 70,
            "match_category": "exploratory",
            "application_status": "unreviewed",
        },
    )
    _write_package(
        project_root,
        "run-20260509",
        "miss",
        {
            "stable_id": "not-eursap",
            "run_id": "run-20260509",
            "title": "Different Source",
            "source": "Other Label",
            "source_url": "https://not-eursap.example/jobs/sap-basis-consultant",
            "match_score": 90,
            "match_category": "strong",
            "application_status": "unreviewed",
        },
    )

    source = SourceRegistryService(project_root).get_source("eursap-jobs")

    assert source is not None
    assert source.stats.jobs_found_total == 1
    assert source.stats.best_recent_match_url == "https://eursap.eu/jobs/sap-basis-consultant"


def test_source_value_matching_prefers_stable_source_id(project_root: Path) -> None:
    seed_source_registry(project_root, EURSAP_SOURCE)
    _write_package(
        project_root,
        "run-20260509",
        "match",
        {
            "stable_id": "eursap-1",
            "source_id": "eursap-jobs",
            "run_id": "run-20260509",
            "title": "SAP Basis Consultant",
            "source": "Unexpected Source Name",
            "source_url": "https://unrelated.example/jobs/sap-basis-consultant",
            "match_score": 77,
            "match_category": "exploratory",
            "application_status": "unreviewed",
        },
    )

    source = SourceRegistryService(project_root).get_source("eursap-jobs")

    assert source is not None
    assert source.stats.jobs_found_total == 1
    assert source.stats.best_recent_match_title == "SAP Basis Consultant"
    assert source.stats.value_status == "promising"


def _write_package(project_root: Path, run_id: str, package_id: str, data: dict) -> None:
    package_dir = project_root / "output" / "2026-05-09" / f"{run_id}-{package_id}"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "index.json").write_text(json.dumps(data), encoding="utf-8")
