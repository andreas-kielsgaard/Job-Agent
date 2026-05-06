from __future__ import annotations

from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from .config import ROOT, load_yaml
from .models import Job


def load_sources(root: Path = ROOT) -> list[dict]:
    config = load_yaml(root / "sources" / "recruiting-sites.yaml")
    return [source for source in config.get("sources", []) if source.get("enabled", True)]


def load_jobs_from_sources(root: Path = ROOT) -> list[Job]:
    jobs: list[Job] = []
    for source in load_sources(root):
        source_type = source.get("type")
        if source_type == "local_yaml":
            jobs.extend(_load_local_yaml(root / source["path"]))
        elif source_type == "search_page":
            jobs.extend(_fetch_search_page(source))
        else:
            continue
    return jobs


def _load_local_yaml(path: Path) -> list[Job]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return [Job.from_mapping(item) for item in data.get("jobs", [])]


def _fetch_search_page(source: dict) -> list[Job]:
    # Generic fetcher for simple pages. Site-specific scrapers can replace this later.
    response = requests.get(source["url"], timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text("\n", strip=True)
    if not text:
        return []
    return [
        Job(
            title=f"Unparsed listing from {source['name']}",
            source=source["name"],
            url=source["url"],
            description=text[:6000],
        )
    ]
