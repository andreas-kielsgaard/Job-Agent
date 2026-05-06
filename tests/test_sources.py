from __future__ import annotations

from pathlib import Path

import pytest
import requests

from job_agent.sources import GenericHtmlAdapter, LocalYamlAdapter, UnsupportedSourceAdapter


class FakeResponse:
    def __init__(self, text: str, error: Exception | None = None) -> None:
        self.text = text
        self.error = error

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
