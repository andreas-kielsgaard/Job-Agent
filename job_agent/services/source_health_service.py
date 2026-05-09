from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.services.recipe_preview_service import RecipePreviewResult

HEALTH_PATH = Path("sources/source-health.yaml")


@dataclass
class SourceHealthRecord:
    source_id: str
    last_preview_at: str = ""
    last_input_type: str = ""
    last_input_value: str = ""
    mode_used: str = ""
    extracted_job_count: int = 0
    useful_titles: int = 0
    generic_labels: int = 0
    unique_urls: int = 0
    average_description_length: int = 0
    warnings: list[str] = field(default_factory=list)
    health_status: str = "untested"
    health_summary: str = "No preview has been saved for this source yet."

    @property
    def warnings_count(self) -> int:
        return len(self.warnings)


class SourceHealthService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.path = root / HEALTH_PATH

    def load_all(self) -> dict[str, SourceHealthRecord]:
        data = read_yaml(self.path, {"sources": {}})
        sources = data.get("sources", {}) if isinstance(data, dict) else {}
        if not isinstance(sources, dict):
            return {}
        return {
            str(source_id): self._record_from_mapping(str(source_id), value)
            for source_id, value in sources.items()
            if isinstance(value, dict)
        }

    def get_health(self, source_id: str) -> SourceHealthRecord:
        return self.load_all().get(source_id, SourceHealthRecord(source_id=source_id))

    def save_preview(self, source_id: str, preview: RecipePreviewResult) -> SourceHealthRecord:
        record = self.record_from_preview(source_id, preview)
        self._save_record(record)
        return record

    def save_failure(self, source_id: str, input_value: str, mode_used: str, warning: str) -> SourceHealthRecord:
        record = SourceHealthRecord(
            source_id=source_id,
            last_preview_at=_now_iso(),
            last_input_value=input_value,
            mode_used=mode_used,
            warnings=[warning],
            health_status="failing",
            health_summary=f"Preview failed: {warning}",
        )
        self._save_record(record)
        return record

    def record_from_preview(self, source_id: str, preview: RecipePreviewResult) -> SourceHealthRecord:
        status = derive_health_status(
            extracted_job_count=preview.extracted_job_count,
            useful_titles=preview.useful_titles,
            generic_labels=preview.generic_labels,
            unique_urls=preview.unique_urls,
            warnings=preview.warnings,
        )
        return SourceHealthRecord(
            source_id=source_id,
            last_preview_at=_now_iso(),
            last_input_type=preview.input_type,
            last_input_value=preview.input_value,
            mode_used=preview.mode_used,
            extracted_job_count=preview.extracted_job_count,
            useful_titles=preview.useful_titles,
            generic_labels=preview.generic_labels,
            unique_urls=preview.unique_urls,
            average_description_length=preview.average_description_length,
            warnings=list(preview.warnings),
            health_status=status,
            health_summary=health_summary(status, preview),
        )

    def _save_record(self, record: SourceHealthRecord) -> None:
        data = read_yaml(self.path, {"sources": {}})
        if not isinstance(data, dict):
            data = {"sources": {}}
        sources = data.setdefault("sources", {})
        if not isinstance(sources, dict):
            sources = {}
            data["sources"] = sources
        sources[record.source_id] = _record_as_mapping(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(self.path, data)

    @staticmethod
    def _record_from_mapping(source_id: str, data: dict[str, Any]) -> SourceHealthRecord:
        return SourceHealthRecord(
            source_id=source_id,
            last_preview_at=str(data.get("last_preview_at") or ""),
            last_input_type=str(data.get("last_input_type") or ""),
            last_input_value=str(data.get("last_input_value") or ""),
            mode_used=str(data.get("mode_used") or ""),
            extracted_job_count=_int_value(data.get("extracted_job_count")),
            useful_titles=_int_value(data.get("useful_titles")),
            generic_labels=_int_value(data.get("generic_labels")),
            unique_urls=_int_value(data.get("unique_urls")),
            average_description_length=_int_value(data.get("average_description_length")),
            warnings=_list_value(data.get("warnings")),
            health_status=str(data.get("health_status") or "untested"),
            health_summary=str(data.get("health_summary") or "No preview has been saved for this source yet."),
        )


def derive_health_status(
    extracted_job_count: int,
    useful_titles: int,
    generic_labels: int,
    unique_urls: int,
    warnings: list[str],
) -> str:
    if extracted_job_count <= 0:
        return "failing"
    if warnings or generic_labels > 0 or unique_urls <= 0:
        return "warning"
    if unique_urls < extracted_job_count:
        return "warning"
    if useful_titles < extracted_job_count:
        return "warning"
    return "good"


def health_summary(status: str, preview: RecipePreviewResult) -> str:
    if status == "good":
        return (
            f"{preview.extracted_job_count} jobs extracted, "
            f"{preview.useful_titles} useful titles, no generic labels."
        )
    if status == "warning":
        parts = [f"{preview.extracted_job_count} jobs extracted"]
        if preview.generic_labels:
            parts.append(f"{preview.generic_labels} generic labels")
        if preview.warnings:
            parts.append(f"{len(preview.warnings)} warnings")
        if preview.unique_urls < preview.extracted_job_count:
            parts.append(f"{preview.unique_urls} unique URLs")
        return ", ".join(parts) + "."
    return "Preview failed or extracted no jobs."


def _record_as_mapping(record: SourceHealthRecord) -> dict[str, Any]:
    return {
        "last_preview_at": record.last_preview_at,
        "last_input_type": record.last_input_type,
        "last_input_value": record.last_input_value,
        "mode_used": record.mode_used,
        "extracted_job_count": record.extracted_job_count,
        "useful_titles": record.useful_titles,
        "generic_labels": record.generic_labels,
        "unique_urls": record.unique_urls,
        "average_description_length": record.average_description_length,
        "warnings": record.warnings,
        "health_status": record.health_status,
        "health_summary": record.health_summary,
    }


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
