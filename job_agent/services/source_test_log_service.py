from __future__ import annotations

import re
import shutil
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from job_agent.config import ROOT
from job_agent.io.atomic import atomic_write_text
from job_agent.io.json_store import write_json


class SourceTestMaterialLog:
    """Source-test evidence bundle.

    The bundle intentionally records fetched page material and derived checks, but
    never reads or copies browser storage-state files.
    """

    def __init__(self, root: Path = ROOT, source_id: str = "") -> None:
        self.root = Path(root)
        self.source_id = _safe_segment(source_id or "source")
        self.artifact_dir = self._new_artifact_dir()
        self.entries: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {}

    @property
    def relative_dir(self) -> str:
        return _relative_path(self.artifact_dir, self.root)

    @property
    def manifest_path(self) -> Path:
        return self.artifact_dir / "manifest.json"

    @property
    def relative_manifest_path(self) -> str:
        return _relative_path(self.manifest_path, self.root)

    def record_source(self, source: dict[str, Any]) -> None:
        self.record_json("source-config.json", _redact_mapping(source), kind="source_config")

    def record_recipe(self, recipe_path: str | Path) -> None:
        path = self.root / recipe_path if not Path(recipe_path).is_absolute() else Path(recipe_path)
        if not path.exists():
            return
        destination = self.artifact_dir / "recipe.yaml"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        self.entries.append(
            {
                "kind": "recipe",
                "path": _relative_path(destination, self.root),
                "source_path": _relative_path(path, self.root),
            }
        )

    def record_text(self, filename: str, content: str, *, kind: str, metadata: dict[str, Any] | None = None) -> str:
        path = self.artifact_dir / _safe_filename(filename)
        atomic_write_text(path, content, encoding="utf-8")
        entry = {"kind": kind, "path": _relative_path(path, self.root), "bytes": len(content.encode("utf-8"))}
        if metadata:
            entry.update(_jsonable(_redact_mapping(metadata)))
        self.entries.append(entry)
        return entry["path"]

    def record_json(self, filename: str, payload: Any, *, kind: str, metadata: dict[str, Any] | None = None) -> str:
        path = self.artifact_dir / _safe_filename(filename)
        write_json(path, _redact_mapping(_jsonable(payload)))
        entry = {"kind": kind, "path": _relative_path(path, self.root)}
        if metadata:
            entry.update(_jsonable(_redact_mapping(metadata)))
        self.entries.append(entry)
        return entry["path"]

    def record_html(
        self,
        *,
        kind: str,
        url: str,
        final_url: str,
        html: str,
        mode: str = "",
        warnings: list[str] | None = None,
        note: str = "",
    ) -> str:
        index = 1 + sum(1 for entry in self.entries if entry.get("kind") in _HTML_KINDS)
        slug = _url_slug(final_url or url)
        prefix = f"{index:03d}-{_safe_segment(kind)}-{slug}"
        html_path = self.artifact_dir / "pages" / f"{prefix}.html"
        text_path = self.artifact_dir / "pages" / f"{prefix}.txt"
        atomic_write_text(html_path, html, encoding="utf-8")
        atomic_write_text(text_path, _visible_text(html), encoding="utf-8")
        entry = {
            "kind": kind,
            "url": url,
            "final_url": final_url,
            "mode": mode,
            "html_path": _relative_path(html_path, self.root),
            "text_path": _relative_path(text_path, self.root),
            "bytes": len(html.encode("utf-8")),
            "warnings": list(warnings or []),
            "note": note,
        }
        self.entries.append(entry)
        return entry["html_path"]

    def finalize(self, result: Any, *, source_run_metadata: dict[str, Any] | None = None) -> None:
        self.summary = {
            "source_id": getattr(result, "source_id", self.source_id),
            "source_name": getattr(result, "source_name", ""),
            "status": getattr(result, "status", ""),
            "job_count": getattr(result, "job_count", 0),
            "warning_count": getattr(result, "warning_count", 0),
            "source_session_used": bool(getattr(result, "source_access_session_used", False)),
            "source_session_scope": getattr(result, "source_access_session_scope", ""),
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.record_json("source-test-result.json", result, kind="source_test_result")
        if source_run_metadata:
            self.record_json("source-run-metadata.json", source_run_metadata, kind="source_run_metadata")
        write_json(
            self.manifest_path,
            {
                "summary": self.summary,
                "artifact_dir": self.relative_dir,
                "entries": self.entries,
            },
        )
        self._write_summary()

    def _new_artifact_dir(self) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        base = self.root / "output" / "source-tests" / self.source_id / timestamp
        path = base
        counter = 1
        while path.exists():
            counter += 1
            path = base.with_name(f"{base.name}-{counter}")
        path.mkdir(parents=True, exist_ok=False)
        return path

    def _write_summary(self) -> None:
        lines = [
            "# Source Test Material Log",
            "",
            f"Source: {self.summary.get('source_name') or self.summary.get('source_id')}",
            f"Status: {self.summary.get('status')}",
            f"Jobs: {self.summary.get('job_count')}",
            f"Warnings: {self.summary.get('warning_count')}",
            f"Connected session used: {'yes' if self.summary.get('source_session_used') else 'no'}",
            "",
            "## Files",
        ]
        for entry in self.entries:
            path = entry.get("path") or entry.get("html_path") or entry.get("text_path")
            label = entry.get("kind", "file")
            detail = entry.get("final_url") or entry.get("url") or entry.get("source_path") or ""
            lines.append(f"- {label}: {path}{f' ({detail})' if detail else ''}")
        atomic_write_text(self.artifact_dir / "summary.md", "\n".join(lines) + "\n", encoding="utf-8")


_HTML_KINDS = {
    "listing",
    "pagination",
    "ajax_pagination",
    "browser_pagination_start",
    "browser_pagination",
    "detail",
    "generic_listing",
}


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _redact_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _sensitive_key(key_text):
                redacted[key_text] = "[redacted]"
            else:
                redacted[key_text] = _redact_mapping(item)
        return redacted
    if isinstance(value, list):
        return [_redact_mapping(item) for item in value]
    return value


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(
        marker in lowered
        for marker in [
            "password",
            "token",
            "secret",
            "cookie",
            "authorization",
            "api_key",
            "storage_state",
            "session_state",
        ]
    )


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_filename(value: str) -> str:
    parts = [_safe_segment(part) for part in Path(value).parts if part not in {"", ".", ".."}]
    if not parts:
        return "file"
    return "/".join(parts)


def _safe_segment(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value).strip()).strip(".-")
    return text[:80] or "item"


def _url_slug(url: str) -> str:
    parsed = urlparse(url)
    seed = "-".join(part for part in [parsed.netloc, parsed.path.strip("/"), parsed.query] if part)
    return _safe_segment(seed or url or "page")
