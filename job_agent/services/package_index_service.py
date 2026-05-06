from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.io.json_store import read_json, write_json


class PackageIndexService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def list_packages(self, run_id: str = "") -> list[dict[str, Any]]:
        packages = []
        status_store = ApplicationStatusStore(self.root)
        for path in (self.root / "output").glob("*/*/index.json"):
            item = read_json(path, None)
            if not isinstance(item, dict):
                continue
            if run_id and item.get("run_id") != run_id:
                continue
            status = status_store.get(item.get("stable_id", ""))
            if status:
                item["application_status"] = status.status
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
            if path.exists():
                result[key] = path.read_text(encoding="utf-8")
        return result

    def mark_package_materials_generated(self, package: dict[str, Any], generated: bool) -> None:
        index_path = package.get("_index_path")
        if not index_path:
            return
        package["materials_generated"] = generated
        write_json(Path(index_path), {key: value for key, value in package.items() if key != "_index_path"})

    def refresh_package_status(self, job_id: str, status: str) -> None:
        for path in (self.root / "output").glob("*/*/index.json"):
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
