from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import requests

from job_agent.digest import write_placeholder_job_package
from job_agent.io.json_store import read_json
from job_agent.models import Job, MatchResult
from job_agent.sources import (
    GenericHtmlAdapter,
    LocalYamlAdapter,
    RecipeHtmlAdapter,
    SourceAdapter,
    UnsupportedSourceAdapter,
    iter_source_results,
    load_jobs_from_sources,
)


class FakeResponse:
    def __init__(self, text: str, error: Exception | None = None, url: str = "https://example.com/jobs") -> None:
        self.text = text
        self.error = error
        self.url = url

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error


def test_local_yaml_adapter_loads_jobs(project_root: Path) -> None:
    path = project_root / "jobs" / "raw" / "sample_jobs.yaml"
    path.write_text(
        "jobs:\n  - title: SAP ABAP Consultant\n    company: Recruiter\n    url: https://example.com/job\n",
        encoding="utf-8",
    )

    result = LocalYamlAdapter({"name": "Local", "path": "jobs/raw/sample_jobs.yaml"}, project_root).fetch()

    assert len(result.jobs) == 1
    assert result.jobs[0].source == "Local"
    assert result.jobs[0].source_confidence == "high"


def test_local_yaml_adapter_attaches_configured_source_id(project_root: Path) -> None:
    path = project_root / "jobs" / "raw" / "sample_jobs.yaml"
    path.write_text(
        "jobs:\n  - title: SAP ABAP Consultant\n    company: Recruiter\n    url: https://example.com/job\n",
        encoding="utf-8",
    )

    result = LocalYamlAdapter(
        {"name": "Local", "source_id": "sample-jobs", "path": "jobs/raw/sample_jobs.yaml"}, project_root
    ).fetch()

    assert result.jobs[0].source_id == "sample-jobs"


def test_generic_html_adapter_warns_when_no_links(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse("<html>No roles here</html>"))

    result = GenericHtmlAdapter({"name": "HTML", "url": "https://example.com"}, project_root).fetch()

    assert not result.jobs
    assert result.warnings


def test_generic_html_adapter_extracts_plausible_job_links(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    html = """
    <ul>
      <li><a href="/jobs/sap-abap">SAP ABAP Consultant contract</a></li>
      <li><a href="/careers/sap-rap">SAP RAP Developer role</a></li>
    </ul>
    """
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse(html))

    result = GenericHtmlAdapter({"name": "HTML", "url": "https://example.com"}, project_root).fetch()

    assert len(result.jobs) == 2
    assert result.jobs[0].application_url.startswith("https://example.com/")


def test_recipe_html_adapter_extracts_jobs_from_recipe(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    recipe_path = _write_recipe(project_root)
    html = """
    <article class="job-card">
      <a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a>
      <span class="location">Remote</span>
      <p class="description">ABAP RAP CDS contract role.</p>
    </article>
    """
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse(html))

    result = RecipeHtmlAdapter(
        {
            "name": "Recipe Source",
            "source_id": "recipe-source",
            "type": "recipe_html",
            "url": "https://example.com/jobs",
            "recipe_path": str(recipe_path.relative_to(project_root)),
        },
        project_root,
    ).fetch()

    assert not result.warnings
    assert len(result.jobs) == 1
    assert result.jobs[0].title == "SAP ABAP Consultant"
    assert result.jobs[0].source == "Recipe Source"
    assert result.jobs[0].source_id == "recipe-source"
    assert result.jobs[0].url == "https://example.com/jobs/sap-abap"
    assert result.jobs[0].location == "Remote"
    assert result.jobs[0].source_confidence == "recipe"


def test_recipe_html_adapter_returns_warning_for_invalid_recipe_path(project_root: Path) -> None:
    result = RecipeHtmlAdapter(
        {
            "name": "Recipe Source",
            "type": "recipe_html",
            "url": "https://example.com/jobs",
            "recipe_path": "sources/recipes/missing.yaml",
        },
        project_root,
    ).fetch()

    assert not result.jobs
    assert len(result.warnings) == 1
    assert "Recipe extraction failed" in result.warnings[0].message


def test_disabled_recipe_html_source_is_not_loaded(project_root: Path) -> None:
    recipe_path = _write_recipe(project_root)
    (project_root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n"
        "  - name: Disabled Recipe\n"
        "    source_id: disabled-recipe\n"
        "    type: recipe_html\n"
        "    url: https://example.com/jobs\n"
        f"    recipe_path: {recipe_path.relative_to(project_root).as_posix()}\n"
        "    enabled: false\n",
        encoding="utf-8",
    )

    result = load_jobs_from_sources(project_root)

    assert not result.jobs
    assert not result.warnings


def test_recipe_html_package_index_includes_source_id(project_root: Path) -> None:
    job = Job(
        title="SAP ABAP Consultant",
        source="Recipe Source",
        source_id="recipe-source",
        url="https://example.com/jobs/sap-abap",
        description="ABAP RAP CDS contract role.",
    )
    match = MatchResult(total_score=82, category="strong")

    paths = write_placeholder_job_package(job, match, date(2026, 5, 9), root=project_root)
    index = read_json(Path(paths["index"]), {})

    assert index["source"] == "Recipe Source"
    assert index["source_id"] == "recipe-source"
    assert index["source_url"] == "https://example.com/jobs/sap-abap"


def test_generic_html_fetch_failure_becomes_warning(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    def fail(*args, **kwargs):
        raise requests.RequestException("timeout")

    monkeypatch.setattr("requests.get", fail)

    result = GenericHtmlAdapter({"name": "HTML", "url": "https://example.com"}, project_root).fetch()

    assert not result.jobs
    assert "Fetch failed" in result.warnings[0].message


def test_unsupported_source_adapter_returns_warning(project_root: Path) -> None:
    result = UnsupportedSourceAdapter({"name": "Mystery", "type": "weird"}, project_root).fetch()

    assert not result.jobs
    assert "Unsupported source type" in result.warnings[0].message


def test_load_jobs_from_sources_emits_started_and_completed_events(project_root: Path) -> None:
    (project_root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n  - name: Local Sample\n    type: local_yaml\n    path: jobs/raw/sample_jobs.yaml\n",
        encoding="utf-8",
    )
    (project_root / "jobs" / "raw" / "sample_jobs.yaml").write_text(
        "jobs:\n  - title: SAP ABAP Consultant\n    company: Recruiter\n    url: https://example.com/job\n",
        encoding="utf-8",
    )
    events = []

    result = load_jobs_from_sources(project_root, progress_callback=events.append)

    assert len(result.jobs) == 1
    assert [event.event_type for event in events] == ["source_started", "source_completed"]
    assert events[0].source_index == 1
    assert events[0].source_count == 1
    assert events[1].jobs_found == 1
    assert events[1].warnings_count == 0


def test_iter_source_results_yields_one_result_per_enabled_source_in_order(project_root: Path) -> None:
    (project_root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n"
        "  - name: First\n"
        "    type: local_yaml\n"
        "    path: jobs/raw/first.yaml\n"
        "  - name: Disabled\n"
        "    type: local_yaml\n"
        "    path: jobs/raw/disabled.yaml\n"
        "    enabled: false\n"
        "  - name: Second\n"
        "    type: local_yaml\n"
        "    path: jobs/raw/second.yaml\n",
        encoding="utf-8",
    )
    (project_root / "jobs" / "raw" / "first.yaml").write_text(
        "jobs:\n  - title: SAP ABAP Consultant\n    url: https://example.com/first\n",
        encoding="utf-8",
    )
    (project_root / "jobs" / "raw" / "second.yaml").write_text(
        "jobs:\n  - title: SAP RAP Consultant\n    url: https://example.com/second\n",
        encoding="utf-8",
    )

    results = list(iter_source_results(project_root))

    assert [result.source_name for result in results] == ["First", "Second"]
    assert [result.source_index for result in results] == [1, 2]
    assert all(result.source_count == 2 for result in results)
    assert [len(result.result.jobs) for result in results] == [1, 1]


def test_load_jobs_from_sources_emits_warning_without_crashing(project_root: Path) -> None:
    (project_root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n  - name: Mystery\n    type: unsupported\n",
        encoding="utf-8",
    )
    events = []

    result = load_jobs_from_sources(project_root, progress_callback=events.append)

    assert not result.jobs
    assert len(result.warnings) == 1
    assert [event.event_type for event in events] == ["source_started", "source_warning", "source_completed"]
    assert events[1].warnings_count == 1
    assert "Unsupported source type" in events[1].message


def test_load_jobs_from_sources_converts_unexpected_adapter_exception_to_failure(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    class BrokenAdapter(SourceAdapter):
        def fetch(self):
            raise RuntimeError("boom")

    (project_root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n  - name: Broken\n    type: local_yaml\n    path: jobs/raw/missing.yaml\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("job_agent.sources.adapter_for_source", lambda source, root: BrokenAdapter(source, root))
    events = []

    result = load_jobs_from_sources(project_root, progress_callback=events.append)

    assert not result.jobs
    assert len(result.warnings) == 1
    assert [event.event_type for event in events] == ["source_started", "source_failed"]
    assert "boom" in events[-1].message


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
