from __future__ import annotations

import builtins
from dataclasses import asdict
from datetime import datetime

import pytest

from job_agent.browser import playwright_probe
from job_agent.browser.playwright_probe import BrowserProbeResult, probe_artifact_dir, probe_url, slugify_url


def test_playwright_probe_module_imports_without_browser_dependency() -> None:
    assert playwright_probe.PLAYWRIGHT_INSTALL_MESSAGE.startswith("Playwright is not installed")


def test_browser_probe_result_is_serializable() -> None:
    result = BrowserProbeResult(
        url="https://example.com",
        final_url="https://example.com/",
        title="Example Domain",
        status=200,
        html_path="output/browser-probes/example/rendered.html",
        text_path="output/browser-probes/example/visible-text.txt",
        link_count=1,
    )

    data = asdict(result)

    assert data["url"] == "https://example.com"
    assert data["status"] == 200
    assert data["link_count"] == 1


def test_missing_playwright_raises_clear_runtime_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("playwright"):
            raise ModuleNotFoundError("No module named 'playwright'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="pip install -r requirements-playwright.txt"):
        probe_url("https://example.com", root=tmp_path)


def test_probe_artifact_dir_uses_timestamp_and_slug(tmp_path) -> None:
    path = probe_artifact_dir(tmp_path, "https://Example.com/jobs?id=123", datetime(2026, 5, 7, 12, 30, 15))

    assert path.exists()
    assert path.name == "20260507-123015-example-com-jobs-id-123"
    assert path.parent == tmp_path / "output" / "browser-probes"


def test_slugify_url_handles_empty_or_punctuation() -> None:
    assert slugify_url("https://Example.com/SAP ABAP?x=1") == "example-com-sap-abap-x-1"
    assert slugify_url("!!!") == "url"
