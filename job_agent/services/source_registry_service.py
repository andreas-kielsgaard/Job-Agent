from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any
from urllib.parse import urlparse

from job_agent.config import ROOT
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.services.package_index_service import PackageIndexService
from job_agent.services.source_health_service import SourceHealthRecord, SourceHealthService

REGISTRY_PATH = Path("sources/source-registry.yaml")
VALID_KINDS = {"manual", "local_yaml", "generic_html", "recipe", "experimental_recipe"}
VALID_STATUSES = {"active", "disabled", "experimental", "needs_review"}


@dataclass
class SourceStats:
    jobs_found_total: int = 0
    strong_matches: int = 0
    exploratory_matches: int = 0
    weak_or_excluded_matches: int = 0
    applied_count: int = 0
    not_interesting_count: int = 0
    unreviewed_count: int = 0
    average_match_score: int = 0
    best_match_score: int = 0
    best_recent_match_title: str = ""
    best_recent_match_url: str = ""
    last_checked: str = ""
    last_run_id: str = ""
    last_successful_extraction: str = ""
    last_successful_run_id: str = ""
    value_status: str = "no_data"
    value_summary: str = "No run data yet."

    @property
    def best_recent_match(self) -> str:
        return self.best_recent_match_title


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
    health: SourceHealthRecord = field(default_factory=lambda: SourceHealthRecord(source_id=""))
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
        source_mappings = self._merged_source_mappings(raw_sources)
        entries = [self._entry_from_mapping(item) for item in source_mappings]
        stats = self._stats_by_source(entries)
        health = SourceHealthService(self.root).load_all()
        for entry in entries:
            entry.stats = stats.get(entry.id, SourceStats())
            entry.health = health.get(entry.id, SourceHealthRecord(source_id=entry.id))
        return entries

    def get_source(self, source_id: str) -> SourceRegistryEntry | None:
        return next((source for source in self.list_sources() if source.id == source_id), None)

    def adopt_recipe_path(self, source_id: str, recipe_path: str, note: str = "") -> SourceRegistryEntry:
        self.ensure_registry()
        data = read_yaml(self.path, {"sources": []})
        if not isinstance(data, dict):
            data = {"sources": []}
        sources = data.setdefault("sources", [])
        if not isinstance(sources, list):
            sources = []
            data["sources"] = sources
        normalized_source_id = _slug(source_id)
        normalized_recipe_path = recipe_path.replace("\\", "/").strip()
        for item in sources:
            if not isinstance(item, dict):
                continue
            if _slug(str(item.get("id") or item.get("name") or "")) != normalized_source_id:
                continue
            item["recipe_path"] = normalized_recipe_path
            if str(item.get("kind") or "") not in {"recipe", "experimental_recipe"}:
                item["kind"] = "experimental_recipe"
            if str(item.get("status") or "") == "needs_review":
                item["status"] = "experimental"
            tags = _list_value(item.get("tags"))
            for tag in ["recipe", "adopted"]:
                if tag not in tags:
                    tags.append(tag)
            item["tags"] = tags
            if note:
                existing_note = str(item.get("notes") or "").strip()
                item["notes"] = f"{existing_note} {note}".strip()
            write_yaml(self.path, data)
            updated = self.get_source(normalized_source_id)
            if not updated:
                raise KeyError(f"Source not found after update: {source_id}")
            return updated
        raise KeyError(f"Source not found: {source_id}")

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

    def _merged_source_mappings(self, raw_sources: list[Any]) -> list[dict[str, Any]]:
        mappings: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            source_id = _mapping_source_id(item)
            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)
            mappings.append(item)

        for item in _default_sources():
            source_id = _mapping_source_id(item)
            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)
            mappings.append(item)

        for item in _recipe_source_mappings(self.root, seen_ids):
            source_id = _mapping_source_id(item)
            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)
            mappings.append(item)
        return mappings

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
            "name": "Whitehall Resources SAP Jobs",
            "kind": "experimental_recipe",
            "status": "experimental",
            "url": "https://www.whitehallresources.com/sap-jobs/",
            "recipe_path": "sources/recipes/experimental/whitehall-sap-contract.yaml",
            "added_at": "2026-05-09",
            "enabled": False,
            "notes": "Live-calibrated experimental recipe using saved Whitehall SAP job-item listing blocks. Not connected to daily runs.",
            "tags": ["sap", "recipe", "live-calibrated"],
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


def _recipe_source_mappings(root: Path, existing_ids: set[str]) -> list[dict[str, Any]]:
    recipes_root = root / "sources" / "recipes"
    if not recipes_root.exists():
        return []

    mappings: list[dict[str, Any]] = []
    for path in sorted(recipes_root.rglob("*.yaml")):
        relative = path.relative_to(root).as_posix()
        if "/examples/" in f"/{relative}":
            continue
        data = read_yaml(path, {})
        if not isinstance(data, dict):
            continue
        start_url = str(data.get("start_url") or "").strip()
        if not start_url:
            continue
        source_id = _slug(path.stem)
        if source_id in existing_ids:
            continue
        mappings.append(
            {
                "id": source_id,
                "name": str(data.get("source_name") or path.stem.replace("-", " ").title()).strip(),
                "kind": "experimental_recipe" if "/experimental/" in f"/{relative}" else "recipe",
                "status": "experimental" if "/experimental/" in f"/{relative}" else "needs_review",
                "url": start_url,
                "recipe_path": relative,
                "added_at": "",
                "enabled": False,
                "notes": "Discovered from a recipe file; review before enabling daily-run execution.",
                "tags": ["recipe"],
            }
        )
    return mappings


def _mapping_source_id(data: dict[str, Any]) -> str:
    return _slug(str(data.get("id") or data.get("name") or "source"))


def _stats_from_packages(packages: list[dict[str, Any]]) -> SourceStats:
    scores = [int(package.get("match_score", 0)) for package in packages if package.get("match_score") is not None]
    sorted_packages = sorted(packages, key=lambda item: (item.get("run_id", ""), item.get("match_score", 0)), reverse=True)
    last_run_id = str(sorted_packages[0].get("run_id", "")) if sorted_packages else ""
    best = max(packages, key=lambda item: int(item.get("match_score", 0)), default={})
    strong_matches = sum(1 for package in packages if package.get("match_category") == "strong")
    exploratory_matches = sum(1 for package in packages if package.get("match_category") == "exploratory")
    weak_or_excluded_matches = sum(
        1 for package in packages if package.get("match_category") in {"weak", "excluded"}
    )
    applied_count = sum(1 for package in packages if package.get("application_status") == "applied")
    not_interesting_count = sum(1 for package in packages if package.get("application_status") == "not_interesting")
    unreviewed_count = sum(1 for package in packages if package.get("application_status", "unreviewed") == "unreviewed")
    average_score = round(mean(scores)) if scores else 0
    best_score = int(best.get("match_score", 0)) if best else 0
    value_status = _derive_value_status(
        jobs_found_total=len(packages),
        strong_matches=strong_matches,
        exploratory_matches=exploratory_matches,
        not_interesting_count=not_interesting_count,
        average_match_score=average_score,
        best_match_score=best_score,
    )
    return SourceStats(
        jobs_found_total=len(packages),
        strong_matches=strong_matches,
        exploratory_matches=exploratory_matches,
        weak_or_excluded_matches=weak_or_excluded_matches,
        applied_count=applied_count,
        not_interesting_count=not_interesting_count,
        unreviewed_count=unreviewed_count,
        average_match_score=average_score,
        best_match_score=best_score,
        best_recent_match_title=str(best.get("title") or ""),
        best_recent_match_url=str(best.get("source_url") or best.get("url") or ""),
        last_checked=last_run_id,
        last_run_id=last_run_id,
        last_successful_extraction=last_run_id if packages else "",
        last_successful_run_id=last_run_id if packages else "",
        value_status=value_status,
        value_summary=_value_summary(
            value_status,
            jobs_found_total=len(packages),
            strong_matches=strong_matches,
            exploratory_matches=exploratory_matches,
            not_interesting_count=not_interesting_count,
            average_match_score=average_score,
        ),
    )


def _package_matches_source(package: dict[str, Any], entry: SourceRegistryEntry) -> bool:
    package_source_id = str(package.get("source_id") or "").strip().lower()
    if package_source_id and package_source_id == entry.id.lower():
        return True
    package_source = str(package.get("source") or "").strip().lower()
    entry_name = entry.name.strip().lower()
    if package_source and package_source == entry_name:
        return True
    if entry.id == "manual-intake" and (
        package_source in {"manual intake", "manual", "manual posting", "recruiter mail", "recruiter email"}
        or package_source.startswith("manual ")
    ):
        return True
    if not entry.url:
        return False
    package_url = str(package.get("source_url") or package.get("url") or "")
    return _same_domain_or_url(entry.url, package_url)


def _derive_value_status(
    *,
    jobs_found_total: int,
    strong_matches: int,
    exploratory_matches: int,
    not_interesting_count: int,
    average_match_score: int,
    best_match_score: int,
) -> str:
    if jobs_found_total == 0:
        return "no_data"
    if not_interesting_count >= max(1, jobs_found_total - 1):
        return "low_value"
    if strong_matches or exploratory_matches or average_match_score >= 65 or best_match_score >= 75:
        return "promising"
    return "mixed"


def _value_summary(
    value_status: str,
    *,
    jobs_found_total: int,
    strong_matches: int,
    exploratory_matches: int,
    not_interesting_count: int,
    average_match_score: int,
) -> str:
    if value_status == "no_data":
        return "No run data yet."
    if value_status == "promising":
        return (
            f"{jobs_found_total} saved jobs with {strong_matches} strong and "
            f"{exploratory_matches} exploratory matches."
        )
    if value_status == "low_value":
        return f"{jobs_found_total} saved jobs, mostly low-value or not interesting."
    return (
        f"{jobs_found_total} saved jobs with average score {average_match_score}; "
        f"{not_interesting_count} marked not interesting."
    )


def _same_domain_or_url(source_url: str, package_url: str) -> bool:
    source = _parsed_url(source_url)
    package = _parsed_url(package_url)
    if not source or not package or not source.netloc or not package.netloc:
        return False
    source_host = _normalize_host(source.netloc)
    package_host = _normalize_host(package.netloc)
    if source_host != package_host:
        return False
    source_path = source.path.rstrip("/")
    package_path = package.path.rstrip("/")
    return not source_path or package_path == source_path or package_path.startswith(f"{source_path}/")


def _parsed_url(value: str):
    parsed = urlparse(value.strip())
    if parsed.netloc:
        return parsed
    return urlparse(f"https://{value.strip()}")


def _normalize_host(host: str) -> str:
    return host.lower().removeprefix("www.")


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _slug(value: str) -> str:
    return "-".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())
