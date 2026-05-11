from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from job_agent.config import ROOT


@dataclass
class RecipeArtifactSummary:
    artifact_dir: str
    display_name: str
    capture_url: str = ""
    capture_mode: str = ""
    modified_at: str = ""
    has_page_html: bool = False
    has_selector_report: bool = False
    candidate_count: int = 0
    top_candidate_selectors: list[str] = field(default_factory=list)
    match_status: str = "other"
    match_reason: str = "Available local artifact."
    warnings: list[str] = field(default_factory=list)


class RecipeArtifactService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.base_dir = self.root / "output" / "recipe-calibration"

    def list_artifacts_for_source(self, source) -> list[RecipeArtifactSummary]:
        artifacts = self.list_artifacts()
        for artifact in artifacts:
            self._apply_match(artifact, getattr(source, "url", ""))
        return sorted(artifacts, key=lambda item: (_match_rank(item.match_status), item.display_name))

    def list_artifacts(self) -> list[RecipeArtifactSummary]:
        if not self.base_dir.exists():
            return []
        artifacts = []
        for path in sorted(item for item in self.base_dir.iterdir() if item.is_dir()):
            artifacts.append(self._summarize(path))
        return artifacts

    def resolve_artifact_path(self, value: str) -> Path:
        if not value.strip():
            raise ValueError("Select a local calibration artifact.")
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve()
            base = self.base_dir.resolve()
        except OSError as exc:
            raise ValueError(f"Invalid artifact path: {value}") from exc
        if resolved != base and base not in resolved.parents:
            raise ValueError("Artifact path must stay under output/recipe-calibration.")
        if not resolved.is_dir():
            raise ValueError(f"Artifact folder not found: {value}")
        return resolved

    def _summarize(self, path: Path) -> RecipeArtifactSummary:
        report = _read_json(path / "selector-report.json")
        candidates = report.get("candidates", []) if isinstance(report.get("candidates"), list) else []
        warnings = []
        if not (path / "page.html").exists():
            warnings.append("Missing page.html.")
        if not (path / "selector-report.json").exists():
            warnings.append("Missing selector-report.json.")
        selectors = [
            str(candidate.get("selector"))
            for candidate in candidates[:5]
            if isinstance(candidate, dict) and candidate.get("selector")
        ]
        relative = path.relative_to(self.root).as_posix() if _is_relative_to(path, self.root) else str(path)
        return RecipeArtifactSummary(
            artifact_dir=relative,
            display_name=path.name,
            capture_url=str(report.get("url") or ""),
            capture_mode=str(report.get("capture_mode") or ""),
            modified_at=_mtime(path),
            has_page_html=(path / "page.html").exists(),
            has_selector_report=(path / "selector-report.json").exists(),
            candidate_count=len(candidates),
            top_candidate_selectors=selectors,
            warnings=warnings,
        )

    def _apply_match(self, artifact: RecipeArtifactSummary, source_url: str) -> None:
        if not source_url or not artifact.capture_url:
            artifact.match_status = "other"
            artifact.match_reason = "No source URL or capture URL to compare."
            return
        if _normalize_url(source_url) == _normalize_url(artifact.capture_url):
            artifact.match_status = "exact"
            artifact.match_reason = "Capture URL matches the source URL."
            return
        if _same_host_path(source_url, artifact.capture_url):
            artifact.match_status = "host_path"
            artifact.match_reason = "Capture URL matches the source host/path."
            return
        artifact.match_status = "other"
        artifact.match_reason = "Capture URL differs from this source; use only if you intend to."


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()


def _match_rank(status: str) -> int:
    return {"exact": 0, "host_path": 1, "other": 2}.get(status, 3)


def _normalize_url(value: str) -> str:
    parsed = _parsed_url(value)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"{host}{path}"


def _same_host_path(source_url: str, artifact_url: str) -> bool:
    source = _parsed_url(source_url)
    artifact = _parsed_url(artifact_url)
    source_host = source.netloc.lower().removeprefix("www.")
    artifact_host = artifact.netloc.lower().removeprefix("www.")
    if not source_host or source_host != artifact_host:
        return False
    source_path = source.path.rstrip("/")
    artifact_path = artifact.path.rstrip("/")
    return not source_path or artifact_path == source_path or artifact_path.startswith(f"{source_path}/")


def _parsed_url(value: str):
    parsed = urlparse(value.strip())
    return parsed if parsed.netloc else urlparse(f"https://{value.strip()}")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
