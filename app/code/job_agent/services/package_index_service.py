from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.io.json_store import read_json, write_json
from job_agent.models import Job
from job_agent.paths import output_dir
from job_agent.store import JobStore

TEXT_PACKAGE_SUFFIXES = {".json", ".md", ".txt", ".html", ".tex"}


class PackageIndexService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def list_packages(self, run_id: str = "") -> list[dict[str, Any]]:
        packages = []
        status_store = ApplicationStatusStore(self.root)
        try:
            statuses = {record.stable_id: record for record in status_store.list_all()}
        except ValueError:
            status_store.recover_corrupt_status_file()
            statuses = {}
        try:
            seen_records = JobStore(self.root, create=False).list_seen_records()
        except ValueError:
            seen_records = []
        seen_by_stable = {record.stable_id: record for record in seen_records}
        seen_by_listing = {record.listing_key: record for record in seen_records if record.listing_key}
        for path in output_dir(self.root).glob("*/*/index.json"):
            item = read_json(path, None)
            if not isinstance(item, dict):
                continue
            if run_id and item.get("run_id") != run_id:
                continue
            status = statuses.get(item.get("stable_id", ""))
            if status:
                item["application_status"] = status.status
            listing_key = str(item.get("listing_key") or self.listing_key_for_package(item))
            item["listing_key"] = listing_key
            seen = seen_by_stable.get(str(item.get("stable_id") or "")) or seen_by_listing.get(listing_key)
            item["posting_status"] = seen.posting_status if seen else str(item.get("posting_status") or "active")
            item["material_status"] = self.material_status(item)
            item["_index_path"] = str(path)
            packages.append(item)
        packages.sort(key=lambda item: (item.get("match_score", 0), item.get("title", "")), reverse=True)
        return packages

    def list_unique_jobs(self) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for package in self.list_packages():
            stable_id = package.get("stable_id") or package.get("package_id")
            existing = by_id.get(stable_id)
            if not existing or package.get("run_id", "") > existing.get("run_id", ""):
                by_id[stable_id] = package
        return sorted(
            by_id.values(), key=lambda item: (item.get("match_score", 0), item.get("title", "")), reverse=True
        )

    def find_package(self, job_id: str, run_id: str = "") -> dict[str, Any] | None:
        for package in self.list_packages(run_id):
            if package.get("stable_id") == job_id or package.get("package_id") == job_id:
                return package
        return None

    def read_package_files(self, package: dict[str, Any] | None) -> dict[str, str]:
        if not package:
            return {}
        result = {}
        for key, path_text in package.get("paths", {}).items():
            path = Path(path_text)
            if path.exists() and path.suffix.lower() in TEXT_PACKAGE_SUFFIXES:
                result[key] = path.read_text(encoding="utf-8")
        return result

    def validate_package(self, package: dict[str, Any]) -> list[str]:
        missing = []
        for key in ["job", "match"]:
            path_text = package.get("paths", {}).get(key)
            if not path_text or not Path(path_text).exists():
                missing.append(key)
        return missing

    def mark_package_materials_generated(self, package: dict[str, Any], generated: bool) -> None:
        index_path = package.get("_index_path")
        if not index_path:
            return
        package["materials_generated"] = generated
        package["material_status"] = "generated" if generated else "missing"
        write_json(Path(index_path), {key: value for key, value in package.items() if key != "_index_path"})

    @staticmethod
    def material_status(package: dict[str, Any]) -> str:
        status = package.get("material_status")
        if status:
            return str(status)
        return "generated" if package.get("materials_generated") else "missing"

    def refresh_package_status(self, job_id: str, status: str) -> None:
        for path in output_dir(self.root).glob("*/*/index.json"):
            item = read_json(path, None)
            if not isinstance(item, dict):
                continue
            if item.get("stable_id") == job_id:
                item["application_status"] = status
                write_json(path, item)

    @staticmethod
    def infer_package_date(package: dict[str, Any]) -> date:
        index_path = Path(package.get("_index_path", ""))
        for parent in index_path.parents:
            try:
                return date.fromisoformat(parent.name)
            except ValueError:
                continue
        return date.today()

    @staticmethod
    def listing_key_for_package(package: dict[str, Any]) -> str:
        return JobStore.listing_key(
            Job(
                title=str(package.get("title") or ""),
                company=str(package.get("company") or "Unknown"),
                source=str(package.get("source") or "Unknown"),
                source_id=str(package.get("source_id") or ""),
                url=str(package.get("source_url") or package.get("url") or ""),
                application_url=str(package.get("application_url") or package.get("source_url") or ""),
            )
        )
