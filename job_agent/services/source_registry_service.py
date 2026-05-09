from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

from job_agent.config import ROOT
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.services.package_index_service import PackageIndexService

REGISTRY_PATH = Path("sources/source-registry.yaml")
VALID_KINDS = {"manual", "local_yaml", "generic_html", "recipe", "experimental_recipe"}
VALID_STATUSES = {"active", "disabled", "experimental", "needs_review"}


@dataclass
class SourceStats:
    jobs_found_total: int = 0
    strong_matches: int = 0
    exploratory_matches: int = 0
    applied_count: int = 0
    not_interesting_count: int = 0
    average_match_score: int = 0
    best_recent_match: str = ""
    last_checked: str = ""
    last_successful_extraction: str = ""


@dataclass
class SourceRegistryEntry:
    id: str
    name: str
    kind: str = "manual"
    status: str = "needs_review"
    url: str = ""
    recipe_path: str = ""
    added_at: str = ""
    enabled: bool = False
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    recipe_state: str = "none"
    stats: SourceStats = field(default_factory=SourceStats)


class SourceRegistryService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.path = root / REGISTRY_PATH

    def ensure_registry(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(self.path, {"sources": _default_sources()})

    def list_sources(self) -> list[SourceRegistryEntry]:
        self.ensure_registry()
        data = read_yaml(self.path, {"sources": []})
        raw_sources = data.get("sources", []) if isinstance(data, dict) else []
        entries = [self._entry_from_mapping(item) for item in raw_sources if isinstance(item, dict)]
        stats = self._stats_by_source(entries)
        for entry in entries:
            entry.stats = stats.get(entry.id, SourceStats())
        return entries

    def get_source(self, source_id: str) -> SourceRegistryEntry | None:
        return next((source for source in self.list_sources() if source.id == source_id), None)

    def _entry_from_mapping(self, data: dict[str, Any]) -> SourceRegistryEntry:
        source_id = _slug(str(data.get("id") or data.get("name") or "source"))
        kind = str(data.get("kind") or "manual").strip()
        status = str(data.get("status") or "needs_review").strip()
        recipe_path = str(data.get("recipe_path") or "").strip()
        return SourceRegistryEntry(
            id=source_id,
            name=str(data.get("name") or source_id).strip(),
            kind=kind if kind in VALID_KINDS else "manual",
            status=status if status in VALID_STATUSES else "needs_review",
            url=str(data.get("url") or "").strip(),
            recipe_path=recipe_path,
            added_at=str(data.get("added_at") or "").strip(),
            enabled=bool(data.get("enabled", False)),
            notes=str(data.get("notes") or "").strip(),
            tags=_list_value(data.get("tags")),
            recipe_state=self._infer_recipe_state(recipe_path, _list_value(data.get("tags")), status),
        )

    def _infer_recipe_state(self, recipe_path: str, tags: list[str], status: str) -> str:
        if not recipe_path:
            return "none"
        lowered_tags = {tag.lower() for tag in tags}
        if "live-calibrated" in lowered_tags:
            return "live-calibrated experimental"
        if "partial" in lowered_tags or status == "needs_review":
            return "partial"
        path = self.root / recipe_path
        if "experimental" in recipe_path.replace("\\", "/").lower():
            return "experimental" if path.exists() else "unknown"
        return "unknown" if not path.exists() else "none"

    def _stats_by_source(self, entries: list[SourceRegistryEntry]) -> dict[str, SourceStats]:
        packages = PackageIndexService(self.root).list_packages()
        result: dict[str, SourceStats] = {}
        for entry in entries:
            matched = [package for package in packages if _package_matches_source(package, entry)]
            result[entry.id] = _stats_from_packages(matched)
        return result


def _default_sources() -> list[dict[str, Any]]:
    return [
        {
            "id": "manual-intake",
            "name": "Manual Intake",
            "kind": "manual",
            "status": "active",
            "url": "",
            "recipe_path": "",
            "added_at": "2026-05-09",
            "enabled": True,
            "notes": "Manual fallback for recruiter emails, copied postings, and sources that are not safe to automate.",
            "tags": ["manual", "fallback"],
        },
        {
            "id": "sample-jobs",
            "name": "Sample Jobs",
            "kind": "local_yaml",
            "status": "active",
            "url": "",
            "recipe_path": "",
            "added_at": "2026-05-09",
            "enabled": True,
            "notes": "Existing local YAML sample source used by daily runs and tests.",
            "tags": ["local", "sample"],
        },
        {
            "id": "eursap-jobs",
            "name": "Eursap Jobs",
            "kind": "experimental_recipe",
            "status": "experimental",
            "url": "https://eursap.eu/jobs",
            "recipe_path": "sources/recipes/experimental/eursap-jobs.yaml",
            "added_at": "2026-05-09",
            "enabled": False,
            "notes": "Live-calibrated experimental recipe from saved local calibration artifacts. Not connected to daily runs.",
            "tags": ["sap", "recipe", "live-calibrated"],
        },
        {
            "id": "whitehall-sap-contract",
            "name": "Whitehall Resources SAP Contract Jobs",
            "kind": "experimental_recipe",
            "status": "experimental",
            "url": "https://www.whitehallresources.com/sap-jobs/contract/",
            "recipe_path": "sources/recipes/experimental/whitehall-sap-contract.yaml",
            "added_at": "2026-05-09",
            "enabled": False,
            "notes": "Live-calibrated experimental recipe using saved Whitehall job-item listing blocks. Not connected to daily runs.",
            "tags": ["sap", "contract", "recipe", "live-calibrated"],
        },
        {
            "id": "montreal-associates-jobs",
            "name": "Montreal Associates Job Search",
            "kind": "experimental_recipe",
            "status": "needs_review",
            "url": "https://www.montrealassociates.com/uk/candidates/job-search/",
            "recipe_path": "sources/recipes/experimental/montreal-associates-jobs.yaml",
            "added_at": "2026-05-09",
            "enabled": False,
            "notes": "Partial rendered experimental recipe. Saved preview works, but broad results include non-SAP roles.",
            "tags": ["sap", "recipe", "partial"],
        },
    ]


def _stats_from_packages(packages: list[dict[str, Any]]) -> SourceStats:
    scores = [int(package.get("match_score", 0)) for package in packages if package.get("match_score") is not None]
    sorted_packages = sorted(packages, key=lambda item: (item.get("run_id", ""), item.get("match_score", 0)), reverse=True)
    last_checked = sorted_packages[0].get("run_id", "") if sorted_packages else ""
    best = max(packages, key=lambda item: int(item.get("match_score", 0)), default={})
    return SourceStats(
        jobs_found_total=len(packages),
        strong_matches=sum(1 for package in packages if package.get("match_category") == "strong"),
        exploratory_matches=sum(1 for package in packages if package.get("match_category") == "exploratory"),
        applied_count=sum(1 for package in packages if package.get("application_status") == "applied"),
        not_interesting_count=sum(1 for package in packages if package.get("application_status") == "not_interesting"),
        average_match_score=round(mean(scores)) if scores else 0,
        best_recent_match=str(best.get("title") or ""),
        last_checked=str(last_checked),
        last_successful_extraction=str(last_checked) if packages else "",
    )


def _package_matches_source(package: dict[str, Any], entry: SourceRegistryEntry) -> bool:
    package_source = str(package.get("source") or "").strip().lower()
    if package_source and package_source == entry.name.lower():
        return True
    if entry.id == "manual-intake" and package_source in {"manual intake", "manual", "recruiter mail"}:
        return True
    source_url = str(package.get("source_url") or package.get("url") or "").lower()
    return bool(entry.url and entry.url.lower().rstrip("/") in source_url)


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _slug(value: str) -> str:
    return "-".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())
