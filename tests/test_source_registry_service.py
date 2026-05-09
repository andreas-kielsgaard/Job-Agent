from __future__ import annotations

from pathlib import Path

from job_agent.services.source_registry_service import SourceRegistryService


def test_default_source_registry_creation_and_listing(project_root: Path) -> None:
    service = SourceRegistryService(project_root)

    sources = service.list_sources()

    assert (project_root / "sources" / "source-registry.yaml").exists()
    names = {source.name for source in sources}
    assert "Manual Intake" in names
    assert "Eursap Jobs" in names
    assert "Whitehall Resources SAP Contract Jobs" in names
    assert "Montreal Associates Job Search" in names


def test_source_registry_normalizes_missing_and_invalid_fields(project_root: Path) -> None:
    registry = project_root / "sources" / "source-registry.yaml"
    registry.write_text(
        "sources:\n"
        "  - name: Strange Source\n"
        "    kind: mystery\n"
        "    status: weird\n"
        "    tags: sap\n",
        encoding="utf-8",
    )

    source = SourceRegistryService(project_root).list_sources()[0]

    assert source.id == "strange-source"
    assert source.kind == "manual"
    assert source.status == "needs_review"
    assert source.tags == ["sap"]
    assert source.recipe_state == "none"


def test_get_source_by_id_and_recipe_status(project_root: Path) -> None:
    service = SourceRegistryService(project_root)

    source = service.get_source("eursap-jobs")

    assert source is not None
    assert source.recipe_path == "sources/recipes/experimental/eursap-jobs.yaml"
    assert source.recipe_state == "live-calibrated experimental"
    assert source.enabled is False


def test_registry_loading_does_not_change_daily_run_source_config(project_root: Path) -> None:
    source_config = project_root / "sources" / "recruiting-sites.yaml"
    source_config.write_text(
        "sources:\n"
        "  - name: Local Sample\n"
        "    type: local_yaml\n"
        "    path: jobs/raw/sample_jobs.yaml\n",
        encoding="utf-8",
    )
    before = source_config.read_text(encoding="utf-8")

    SourceRegistryService(project_root).list_sources()

    assert source_config.read_text(encoding="utf-8") == before
