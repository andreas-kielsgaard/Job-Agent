from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from job_agent.application_models import ApplicationRecord
from job_agent.application_status_store import ApplicationStatusRecord, ApplicationStatusStore
from job_agent.application_store import ApplicationStore
from job_agent.config import ROOT
from job_agent.run_store import utc_now
from job_agent.services.package_index_service import PackageIndexService


class ApplicationTrackerService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.applications = ApplicationStore(self.root)
        self.statuses = ApplicationStatusStore(self.root)
        self.packages = PackageIndexService(self.root)

    def ensure_from_job(self, job_id: str) -> ApplicationRecord:
        status = self.statuses.get(job_id)
        package = self.packages.find_package(job_id)
        if status is None and not package:
            raise KeyError(job_id)
        package_status = str((package or {}).get("application_status") or "")
        if status is not None and status.status != "applied" and package_status != "applied":
            existing = self.applications.get(job_id)
            if existing:
                return existing
            raise ValueError(f"Job is not marked applied: {job_id}")
        return self.ensure_from_package_or_status(package, status)

    def backfill_applied(self) -> list[ApplicationRecord]:
        records: list[ApplicationRecord] = []
        seen: set[str] = set()
        for status in self.statuses.list_all():
            if status.status != "applied":
                continue
            package = self.packages.find_package(status.stable_id)
            record = self.ensure_from_package_or_status(package, status)
            records.append(record)
            seen.add(record.application_id)
        for package in self.packages.list_unique_jobs():
            if str(package.get("application_status") or "") != "applied":
                continue
            application_id = str(package.get("stable_id") or package.get("package_id") or "")
            if not application_id or application_id in seen:
                continue
            record = self.ensure_from_package_or_status(package, self.statuses.get(application_id))
            records.append(record)
            seen.add(record.application_id)
        return records

    def ensure_from_package_or_status(
        self,
        package: dict[str, Any] | None,
        status: ApplicationStatusRecord | None = None,
    ) -> ApplicationRecord:
        application_id = _text(
            (package or {}).get("stable_id"),
            (package or {}).get("package_id"),
            status.stable_id if status else "",
        )
        if not application_id:
            raise KeyError("Application record needs a stable job id.")
        existing = self.applications.get(application_id)
        job_payload = _job_payload(self.packages.read_package_files(package).get("job", "")) if package else {}
        applied_at = (
            (status.applied_at if status else "")
            or _text((package or {}).get("applied_at"))
            or (str(self.packages.infer_package_date(package)) if package else "")
            or utc_now()
        )
        record = ApplicationRecord(
            application_id=application_id,
            stable_id=application_id,
            fuzzy_key=_text((package or {}).get("fuzzy_key"), status.fuzzy_key if status else ""),
            title=_text((package or {}).get("title"), status.title if status else "", job_payload.get("title")),
            company=_text((package or {}).get("company"), status.company if status else "", job_payload.get("company")),
            recruiter=_text((package or {}).get("recruiter"), job_payload.get("recruiter")),
            end_client=_text((package or {}).get("end_client"), job_payload.get("end_client")),
            source=_text((package or {}).get("source"), status.source if status else "", job_payload.get("source")),
            source_id=_text((package or {}).get("source_id"), job_payload.get("source_id")),
            source_url=_text(
                (package or {}).get("source_url"),
                (package or {}).get("url"),
                status.url if status else "",
                job_payload.get("url"),
            ),
            application_url=_text(
                (package or {}).get("application_url"),
                status.application_url if status else "",
                job_payload.get("application_url"),
            ),
            description_preview=_clip(_text(job_payload.get("description"), job_payload.get("raw_text")), 420),
            applied_at=applied_at,
            outcome=existing.outcome if existing else "open",
            notes=existing.notes if existing else "",
            latest_run_id=_text((package or {}).get("run_id"), existing.latest_run_id if existing else ""),
            created_at=existing.created_at if existing else "",
        )
        if existing:
            record = replace(record, updated_at=existing.updated_at)
        return self.applications.upsert(record)


def _job_payload(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _text(*values: Any) -> str:
    for value in values:
        text = "" if value is None else str(value).strip()
        if text:
            return text
    return ""


def _clip(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "."
