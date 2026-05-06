from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

from .config import ROOT, load_yaml
from .models import Job, SourceRunResult, SourceWarning


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

    JOB_HINTS = ("job", "career", "vacancy", "contract", "sap", "abap", "consultant")

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

        soup = BeautifulSoup(response.text, "html.parser")
        jobs = self._extract_link_listings(soup, url, source_name)
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

    def _extract_link_listings(self, soup: BeautifulSoup, base_url: str, source_name: str) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()
        for link in soup.find_all("a", href=True):
            title = link.get_text(" ", strip=True)
            href = urljoin(base_url, link["href"])
            haystack = f"{title} {href}".lower()
            if len(title) < 8 or not any(hint in haystack for hint in self.JOB_HINTS):
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
        return jobs[: self.source.get("max_results", 25)]


class WhitehallResourcesAdapter(GenericHtmlAdapter):
    """Initial site-specific hook.

    The implementation currently uses generic link extraction, but keeping the
    adapter separate gives this source a natural place for selectors once tested.
    """


def load_sources(root: Path = ROOT) -> list[dict]:
    config = load_yaml(root / "sources" / "recruiting-sites.yaml")
    return [source for source in config.get("sources", []) if source.get("enabled", True)]


def load_jobs_from_sources(root: Path = ROOT) -> SourceRunResult:
    result = SourceRunResult()
    for source in load_sources(root):
        adapter = adapter_for_source(source, root)
        source_result = adapter.fetch()
        result.jobs.extend(source_result.jobs)
        result.warnings.extend(source_result.warnings)
    return result


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
