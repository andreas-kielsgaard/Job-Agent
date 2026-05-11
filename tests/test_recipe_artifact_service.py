from __future__ import annotations

import json
from pathlib import Path

from job_agent.services.recipe_artifact_service import RecipeArtifactService
from job_agent.services.source_registry_service import SourceRegistryEntry


def test_artifact_discovery_summarizes_complete_artifacts(project_root: Path) -> None:
    artifact = _write_artifact(project_root, "eursap", "https://eursap.eu/jobs")

    summaries = RecipeArtifactService(project_root).list_artifacts()

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.artifact_dir == artifact.relative_to(project_root).as_posix()
    assert summary.capture_url == "https://eursap.eu/jobs"
    assert summary.capture_mode == "static_html"
    assert summary.has_page_html is True
    assert summary.has_selector_report is True
    assert summary.candidate_count == 1
    assert summary.top_candidate_selectors == ["a.looking__card"]


def test_artifact_discovery_handles_missing_files_with_warnings(project_root: Path) -> None:
    artifact = project_root / "output" / "recipe-calibration" / "partial"
    artifact.mkdir(parents=True)

    summary = RecipeArtifactService(project_root).list_artifacts()[0]

    assert summary.display_name == "partial"
    assert summary.has_page_html is False
    assert summary.has_selector_report is False
    assert "Missing page.html." in summary.warnings
    assert "Missing selector-report.json." in summary.warnings


def test_artifact_matching_prefers_source_url_match(project_root: Path) -> None:
    _write_artifact(project_root, "other", "https://example.com/jobs")
    _write_artifact(project_root, "eursap", "https://eursap.eu/jobs")
    source = SourceRegistryEntry(
        id="eursap-jobs",
        name="Eursap Jobs",
        url="https://eursap.eu/jobs",
        recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
    )

    summaries = RecipeArtifactService(project_root).list_artifacts_for_source(source)

    assert summaries[0].display_name == "eursap"
    assert summaries[0].match_status == "exact"
    assert summaries[1].match_status == "other"


def _write_artifact(project_root: Path, name: str, url: str) -> Path:
    artifact = project_root / "output" / "recipe-calibration" / name
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "page.html").write_text("<html></html>", encoding="utf-8")
    (artifact / "selector-report.json").write_text(
        json.dumps(
            {
                "url": url,
                "capture_mode": "static_html",
                "candidates": [{"selector": "a.looking__card"}],
            }
        ),
        encoding="utf-8",
    )
    return artifact
