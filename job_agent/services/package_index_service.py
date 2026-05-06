from __future__ import annotations

import html
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
        for path in (self.root / "output").glob("*/*/index.json"):
            item = read_json(path, None)
            if not isinstance(item, dict):
                continue
            if run_id and item.get("run_id") != run_id:
                continue
            status = ApplicationStatusStore(self.root).get(item.get("stable_id", ""))
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
        return sorted(by_id.values(), key=lambda item: (item.get("match_score", 0), item.get("title", "")), reverse=True)

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

    def build_review_bundle(self, package: dict[str, Any], files: dict[str, str], status: Any) -> str:
        parts = [
            "# External Agent Review Bundle",
            "",
            "Please review and suggest improvements to the application materials below. Keep claims accurate and do not invent experience.",
            "",
            "## Role",
            f"Title: {package.get('title', '')}",
            f"Company/recruiter: {package.get('company', '')} / {package.get('recruiter', '')}",
            f"Location: {package.get('location', '')}",
            f"Remote/onsite: {package.get('remote', '')}",
            f"Rate: {package.get('rate', '')}",
            f"Source URL: {package.get('source_url', '')}",
            f"Application URL: {package.get('application_url', '')}",
            "",
            "## Match",
            f"Score/category: {package.get('match_score', '')}% / {package.get('match_category', '')}",
            f"Recommended angle: {package.get('recommended_angle', '')}",
            "Concerns: " + "; ".join(package.get("concerns", [])),
            f"Application status: {getattr(status, 'status', 'unreviewed') if status else 'unreviewed'}",
            "",
            "## Job JSON",
            files.get("job", ""),
            "",
            "## Match Analysis",
            files.get("match_analysis", "[Not generated yet]"),
            "",
            "## At-a-glance CV",
            files.get("cv", "[Not generated yet]"),
            "",
            "## Application Text",
            files.get("application", "[Not generated yet]"),
            "",
            "## Form Answers",
            files.get("form_answers", "[Not generated yet]"),
        ]
        return "\n".join(parts)

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


def markdown_to_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = escaped.replace("\n### ", "\n<h3>").replace("\n## ", "\n<h2>").replace("\n# ", "\n<h1>")
    lines = escaped.splitlines()
    html_lines = []
    for line in lines:
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("- "):
            html_lines.append(f'<p class="bullet">{line}</p>')
        elif line.strip():
            html_lines.append(f"<p>{line}</p>")
    return "\n".join(html_lines)
