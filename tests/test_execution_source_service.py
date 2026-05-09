from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.source_registry_service import SourceRegistryService


def test_execution_service_loads_existing_sources(project_root: Path) -> None:
    config = project_root / "sources" / "recruiting-sites.yaml"
    config.write_text(
        "sources:\n"
        "  - name: Local Sample\n"
        "    type: local_yaml\n"
        "    path: jobs/raw/sample_jobs.yaml\n",
        encoding="utf-8",
    )

    sources = ExecutionSourceService(project_root).list_sources()

    assert sources[0]["name"] == "Local Sample"
    assert sources[0]["type"] == "local_yaml"


def test_create_disabled_recipe_execution_entry_from_registry_source(project_root: Path) -> None:
    source = SourceRegistryService(project_root).get_source("eursap-jobs")

    result = ExecutionSourceService(project_root).create_or_update_recipe_source(source)
    entry = ExecutionSourceService(project_root).find_by_source_id("eursap-jobs")

    assert result.created is True
    assert entry == {
        "name": "Eursap Jobs",
        "source_id": "eursap-jobs",
        "type": "recipe_html",
        "url": "https://eursap.eu/jobs",
        "recipe_path": "sources/recipes/experimental/eursap-jobs.yaml",
        "enabled": False,
    }


def test_update_execution_entry_does_not_duplicate_and_keeps_disabled(project_root: Path) -> None:
    service = ExecutionSourceService(project_root)
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    service.create_or_update_recipe_source(source)
    service.enable("eursap-jobs")

    result = service.create_or_update_recipe_source(source)
    entries = [item for item in service.list_sources() if item.get("source_id") == "eursap-jobs"]

    assert result.updated is True
    assert len(entries) == 1
    assert entries[0]["enabled"] is False


def test_enable_and_disable_execution_entry(project_root: Path) -> None:
    service = ExecutionSourceService(project_root)
    source = SourceRegistryService(project_root).get_source("eursap-jobs")
    service.create_or_update_recipe_source(source)

    enabled = service.enable("eursap-jobs")
    disabled = service.disable("eursap-jobs")

    assert enabled["enabled"] is True
    assert disabled["enabled"] is False


def test_manual_source_cannot_create_recipe_execution_entry(project_root: Path) -> None:
    source = SourceRegistryService(project_root).get_source("manual-intake")

    with pytest.raises(ValueError):
        ExecutionSourceService(project_root).create_or_update_recipe_source(source)


def test_enable_missing_execution_entry_raises(project_root: Path) -> None:
    with pytest.raises(KeyError):
        ExecutionSourceService(project_root).enable("eursap-jobs")
