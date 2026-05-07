from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

from .config import ROOT, load_yaml
from .models import Job, SourceRunResult, SourceWarning


@dataclass
class SourceProgressEvent:
    event_type: str
    source_name: str
    source_index: int
    source_count: int
    message: str
    source_type: str = ""
    source_url: str = ""
    jobs_found: int = 0
    warnings_count: int = 0
    elapsed_time_seconds: float | None = None


SourceProgressCallback = Callable[[SourceProgressEvent], None]


@dataclass
class SourceFetchResult:
    source: dict
    source_name: str
    source_index: int
    source_count: int
    result: SourceRunResult
    elapsed_time_seconds: float | None = None


class SourceAdapter(ABC):
    def __init__(self, source: dict, root: Path = ROOT) -> None:
        self.source = source
        self.root = root

    @abstractmethod
    def fetch(self) -> SourceRunResult:
        raise NotImplementedError


class LocalYamlAdapter(SourceAdapter):
    def fetch(self) -> SourceRunResult:
        path = self.root / self.source["path"]
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        jobs = []
        today = str(date.today())
        for item in data.get("jobs", []):
            item.setdefault("source", self.source.get("name", "Local YAML"))
            item.setdefault("first_seen_date", today)
            item.setdefault("source_confidence", "high")
            item.setdefault("freshness_confidence", "explicit" if item.get("posted_date") else "unknown")
            jobs.append(Job.from_mapping(item))
        return SourceRunResult(jobs=jobs)


class GenericHtmlAdapter(SourceAdapter):
    """Best-effort public HTML extractor.

    This adapter is intentionally conservative. If it cannot find plausible listing
    links, it returns a source warning instead of manufacturing a fake job from the
    whole page.
    """

    def fetch(self) -> SourceRunResult:
        url = self.source.get("url", "")
        source_name = self.source.get("name", "Generic HTML")
        if not url:
            return SourceRunResult(warnings=[SourceWarning(source_name, "Source has no URL.")])

        try:
            response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
        except requests.RequestException as exc:
            return SourceRunResult(warnings=[SourceWarning(source_name, f"Fetch failed: {exc}", url)])

        jobs = extract_generic_jobs_from_html(
            response.text,
            base_url=url,
            source_name=source_name,
            max_results=self.source.get("max_results", 25),
        )
        if not jobs:
            return SourceRunResult(
                warnings=[
                    SourceWarning(
                        source_name,
                        "Generic HTML adapter found no plausible job links. Add a site-specific adapter or selectors.",
                        url,
                    )
                ]
            )
        return SourceRunResult(jobs=jobs)


class WhitehallResourcesAdapter(GenericHtmlAdapter):
    """Initial site-specific hook.

    The implementation currently uses generic link extraction, but keeping the
    adapter separate gives this source a natural place for selectors once tested.
    """


def load_sources(root: Path = ROOT) -> list[dict]:
    config = load_yaml(root / "sources" / "recruiting-sites.yaml")
    return [source for source in config.get("sources", []) if source.get("enabled", True)]


def load_jobs_from_sources(
    root: Path = ROOT,
    progress_callback: SourceProgressCallback | None = None,
) -> SourceRunResult:
    result = SourceRunResult()
    for source_result in iter_source_results(root, progress_callback=progress_callback):
        result.jobs.extend(source_result.result.jobs)
        result.warnings.extend(source_result.result.warnings)
    return result


def iter_source_results(
    root: Path = ROOT,
    progress_callback: SourceProgressCallback | None = None,
):
    sources = load_sources(root)
    source_count = len(sources)
    for source_index, source in enumerate(sources, start=1):
        source_name = source.get("name", "Unknown")
        source_type = source.get("type", "")
        source_url = source.get("url") or source.get("path", "")
        started_at = perf_counter()
        _emit_source_progress(
            progress_callback,
            SourceProgressEvent(
                event_type="source_started",
                source_name=source_name,
                source_index=source_index,
                source_count=source_count,
                source_type=source_type,
                source_url=source_url,
                message=f"Checking source {source_index}/{source_count}: {source_name}",
            ),
        )
        adapter = adapter_for_source(source, root)
        try:
            source_result = adapter.fetch()
        except Exception as exc:
            elapsed = round(perf_counter() - started_at, 3)
            warning = SourceWarning(source_name, f"Source failed unexpectedly: {exc}", source_url)
            source_result = SourceRunResult(warnings=[warning])
            _emit_source_progress(
                progress_callback,
                SourceProgressEvent(
                    event_type="source_failed",
                    source_name=source_name,
                    source_index=source_index,
                    source_count=source_count,
                    source_type=source_type,
                    source_url=source_url,
                    warnings_count=1,
                    elapsed_time_seconds=elapsed,
                    message=f"Source failed: {source_name} - {exc}",
                ),
            )
            yield SourceFetchResult(
                source=source,
                source_name=source_name,
                source_index=source_index,
                source_count=source_count,
                result=source_result,
                elapsed_time_seconds=elapsed,
            )
            continue
        elapsed = round(perf_counter() - started_at, 3)
        for warning in source_result.warnings:
            _emit_source_progress(
                progress_callback,
                SourceProgressEvent(
                    event_type="source_warning",
                    source_name=warning.source,
                    source_index=source_index,
                    source_count=source_count,
                    source_type=source_type,
                    source_url=warning.url or source_url,
                    warnings_count=1,
                    elapsed_time_seconds=elapsed,
                    message=f"Source warning from {warning.source}: {warning.message}",
                ),
            )
        _emit_source_progress(
            progress_callback,
            SourceProgressEvent(
                event_type="source_completed",
                source_name=source_name,
                source_index=source_index,
                source_count=source_count,
                source_type=source_type,
                source_url=source_url,
                jobs_found=len(source_result.jobs),
                warnings_count=len(source_result.warnings),
                elapsed_time_seconds=elapsed,
                message=(
                    f"Completed source {source_index}/{source_count}: {source_name} - "
                    f"{len(source_result.jobs)} jobs found, {len(source_result.warnings)} warnings"
                ),
            ),
        )
        yield SourceFetchResult(
            source=source,
            source_name=source_name,
            source_index=source_index,
            source_count=source_count,
            result=source_result,
            elapsed_time_seconds=elapsed,
        )


def _emit_source_progress(callback: SourceProgressCallback | None, event: SourceProgressEvent) -> None:
    if callback:
        callback(event)


def adapter_for_source(source: dict, root: Path = ROOT) -> SourceAdapter:
    source_type = source.get("type")
    name = source.get("name", "").lower()
    if source_type == "local_yaml":
        return LocalYamlAdapter(source, root)
    if "whitehall" in name:
        return WhitehallResourcesAdapter(source, root)
    if source_type in {"search_page", "generic_html"}:
        return GenericHtmlAdapter(source, root)
    return UnsupportedSourceAdapter(source, root)


class UnsupportedSourceAdapter(SourceAdapter):
    def fetch(self) -> SourceRunResult:
        return SourceRunResult(
            warnings=[
                SourceWarning(
                    self.source.get("name", "Unknown"),
                    f"Unsupported source type: {self.source.get('type', 'missing')}.",
                    self.source.get("url", ""),
                )
            ]
        )


JOB_HINTS = ("job", "career", "vacancy", "contract", "sap", "abap", "consultant")


def extract_generic_jobs_from_html(
    html: str,
    base_url: str,
    source_name: str,
    max_results: int = 25,
) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    for link in soup.find_all("a", href=True):
        title = link.get_text(" ", strip=True)
        href = urljoin(base_url, link["href"])
        haystack = f"{title} {href}".lower()
        if len(title) < 8 or not any(hint in haystack for hint in JOB_HINTS):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)
        surrounding = link.find_parent(["article", "li", "div", "section"])
        raw_text = surrounding.get_text("\n", strip=True) if surrounding else title
        jobs.append(
            Job(
                title=title,
                source=source_name,
                url=href,
                application_url=href,
                description=raw_text[:3000],
                raw_text=raw_text[:5000],
                source_confidence="medium",
                freshness_confidence="unknown",
                extraction_notes=["Generic HTML link extraction; verify details manually."],
            )
        )
    return jobs[:max_results]
