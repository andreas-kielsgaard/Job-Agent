from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from job_agent.application_models import (
    APPLICATION_OUTCOMES,
    COMMUNICATION_CHANNELS,
    COMMUNICATION_DIRECTIONS,
    ApplicationRecord,
    EmailThreadLink,
    ManualCommunicationEvent,
)
from job_agent.application_store import ApplicationStore, EmailThreadLinkStore, ManualCommunicationEventStore
from job_agent.config import ROOT
from job_agent.services.application_tracker_service import ApplicationTrackerService
from job_agent.services.package_index_service import PackageIndexService

MATERIAL_SECTIONS = [
    ("focused_cv", "Focused one-page CV", "CV"),
    ("application", "Application text", "Application"),
    ("form_answers", "Form answers", "Forms"),
    ("match_analysis", "Match analysis", "Analysis"),
]


class ApplicationIndexService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.applications = ApplicationStore(self.root)
        self.thread_links = EmailThreadLinkStore(self.root)
        self.manual_events = ManualCommunicationEventStore(self.root)
        self.packages = PackageIndexService(self.root)
        self.tracker = ApplicationTrackerService(self.root)

    def list_rows(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.tracker.backfill_applied()
        rows = [self._row(record) for record in self.applications.list_all()]
        return self._filter_rows(rows, filters or {})

    def detail(self, application_id: str) -> dict[str, Any]:
        self.tracker.backfill_applied()
        record = self.applications.get(application_id)
        if record is None:
            raise KeyError(application_id)
        package = self.packages.find_package(record.stable_id)
        files = self.packages.read_package_files(package)
        job_payload = _json_payload(files.get("job", ""))
        links = self.thread_links.list_for_application(record.application_id)
        events = self.manual_events.list_for_application(record.application_id)
        return {
            "title": f"Application - {record.title or record.application_id}",
            "application": self._application_payload(record, links, events),
            "package": package or {},
            "files": files,
            "job_detail": self._job_detail(record, package or {}, job_payload),
            "thread_links": [self._thread_payload(link) for link in links],
            "manual_events": [self._event_payload(event) for event in events],
            "material_sections": self._material_sections(package or {}, files),
            "outcomes": sorted(APPLICATION_OUTCOMES),
            "channels": sorted(COMMUNICATION_CHANNELS),
            "directions": sorted(COMMUNICATION_DIRECTIONS),
            "application_options": self._application_options(record.application_id),
        }

    def _row(self, record: ApplicationRecord) -> dict[str, Any]:
        links = self.thread_links.list_for_application(record.application_id)
        events = self.manual_events.list_for_application(record.application_id)
        linked_count = len([link for link in links if link.status == "linked"])
        rejected_count = len([link for link in links if link.status == "rejected"])
        latest_event = events[0] if events else None
        preview_events = list(reversed(events[:3]))
        communication_state = "linked" if linked_count else "manual events" if events else "no thread linked"
        return {
            "application_id": record.application_id,
            "detail_url": f"/applications/{quote(record.application_id, safe='')}",
            "title": record.title or "Untitled application",
            "company": record.company or "Unknown",
            "recruiter": record.recruiter,
            "source": record.source,
            "description_preview": record.description_preview,
            "applied_at": _display_date(record.applied_at),
            "outcome": record.outcome,
            "communication_state": communication_state,
            "linked_thread_count": linked_count,
            "rejected_thread_count": rejected_count,
            "latest_event": self._event_payload(latest_event) if latest_event else {},
            "preview_events": [self._event_payload(event) for event in preview_events],
            "last_activity_at": _display_date(latest_event.occurred_at if latest_event else ""),
            "last_activity_age": _age_label(latest_event.occurred_at if latest_event else ""),
        }

    def _application_payload(
        self,
        record: ApplicationRecord,
        links: list[EmailThreadLink],
        events: list[ManualCommunicationEvent],
    ) -> dict[str, Any]:
        row = self._row(record)
        row.update(
            {
                "stable_id": record.stable_id,
                "fuzzy_key": record.fuzzy_key,
                "end_client": record.end_client,
                "source_id": record.source_id,
                "source_url": record.source_url,
                "application_url": record.application_url,
                "latest_run_id": record.latest_run_id,
                "created_at": _display_date(record.created_at),
                "updated_at": _display_date(record.updated_at),
                "has_links": any(link.status == "linked" for link in links),
                "has_manual_events": bool(events),
            }
        )
        return row

    def _job_detail(
        self,
        record: ApplicationRecord,
        package: dict[str, Any],
        job_payload: dict[str, Any],
    ) -> dict[str, Any]:
        description = _text(job_payload.get("description"), job_payload.get("raw_text"), record.description_preview)
        facts = [
            ("Company", record.company or package.get("company")),
            ("Recruiter", record.recruiter or package.get("recruiter")),
            ("End client", record.end_client or package.get("end_client")),
            ("Source", record.source or package.get("source")),
            ("Location", job_payload.get("location") or package.get("location")),
            ("Remote", job_payload.get("remote") or package.get("remote")),
            ("Rate", job_payload.get("rate") or package.get("rate")),
            ("Workload", job_payload.get("workload") or package.get("workload")),
            ("Duration", job_payload.get("contract_duration") or package.get("contract_duration")),
            ("Start", job_payload.get("start_date") or package.get("start_date")),
        ]
        return {
            "description": description,
            "facts": [{"label": label, "value": _text(value)} for label, value in facts if _has_real_value(value)],
            "source_url": record.source_url or _text(package.get("source_url"), package.get("url")),
            "application_url": record.application_url or _text(package.get("application_url")),
            "job_detail_url": self._job_detail_url(record, package),
        }

    def _thread_payload(self, link: EmailThreadLink) -> dict[str, str]:
        return {
            "link_id": link.link_id,
            "application_id": link.application_id,
            "provider": link.provider,
            "account_id": link.account_id,
            "thread_id": link.thread_id,
            "status": link.status,
            "linked_by": link.linked_by,
            "rejected_reason": link.rejected_reason,
            "created_at": _display_date(link.created_at),
            "updated_at": _display_date(link.updated_at),
        }

    def _event_payload(self, event: ManualCommunicationEvent | None) -> dict[str, Any]:
        if event is None:
            return {}
        direction_label = {"outbound": "You", "inbound": "Them", "note": "Note"}.get(event.direction, "Note")
        summary = event.subject or event.note or event.contact
        return {
            "event_id": event.event_id,
            "application_id": event.application_id,
            "channel": event.channel,
            "direction": event.direction,
            "direction_label": direction_label,
            "occurred_at": _display_date(event.occurred_at),
            "contact": event.contact,
            "subject": event.subject,
            "note": event.note,
            "summary": summary,
            "created_at": _display_date(event.created_at),
        }

    def _material_sections(self, package: dict[str, Any], files: dict[str, str]) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        for key, label, short_label in MATERIAL_SECTIONS:
            content = files.get(key, "")
            url = self._package_file_url(package, key)
            sections.append(
                {
                    "key": key,
                    "label": label,
                    "short_label": short_label,
                    "content": content,
                    "url": url,
                    "download_url": self._package_file_url(package, key, download=True),
                    "status": "ready" if content.strip() or url else "missing",
                }
            )
        return sections

    def _application_options(self, current_application_id: str) -> list[dict[str, str]]:
        return [
            {"application_id": item.application_id, "label": f"{item.title or item.application_id} - {item.company}"}
            for item in self.applications.list_all()
            if item.application_id != current_application_id
        ]

    def _job_detail_url(self, record: ApplicationRecord, package: dict[str, Any]) -> str:
        if not package:
            return ""
        url = f"/jobs/{quote(record.stable_id, safe='')}"
        run_id = _text(package.get("run_id"), record.latest_run_id)
        return f"{url}?{urlencode({'run_id': run_id})}" if run_id else url

    def _package_file_url(self, package: dict[str, Any], key: str, *, download: bool = False) -> str:
        path_text = str(package.get("paths", {}).get(key) or "")
        if not path_text:
            return ""
        stable_id = quote(str(package.get("stable_id") or package.get("package_id") or ""), safe="")
        if not stable_id:
            return ""
        query = {}
        if package.get("run_id"):
            query["run_id"] = str(package.get("run_id"))
        if download:
            query["download"] = "1"
        suffix = f"?{urlencode(query)}" if query else ""
        return f"/jobs/{stable_id}/files/{key}{suffix}"

    def _filter_rows(self, rows: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
        outcome = str(filters.get("outcome") or "").strip()
        communication = str(filters.get("communication") or "").strip()
        query = str(filters.get("q") or "").strip().lower()
        result = []
        for row in rows:
            if outcome and row["outcome"] != outcome:
                continue
            if communication and row["communication_state"] != communication:
                continue
            if query:
                haystack = " ".join(
                    [
                        row["title"],
                        row["company"],
                        row["recruiter"],
                        row["source"],
                        row["description_preview"],
                    ]
                ).lower()
                if query not in haystack:
                    continue
            result.append(row)
        return result


def _json_payload(text: str) -> dict[str, Any]:
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


def _display_date(value: Any) -> str:
    text = _text(value)
    if "T" in text:
        return text[:16].replace("T", " ")
    return text


def _has_real_value(value: Any) -> bool:
    text = _text(value)
    return bool(text) and text.lower() not in {"unknown", "not listed", "n/a", "none", "-"}


def _age_label(value: str) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        normalized = text.replace("Z", "+00:00")
        when = datetime.fromisoformat(normalized)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
    except ValueError:
        return ""
    delta = datetime.now(UTC) - when.astimezone(UTC)
    days = max(0, delta.days)
    if days == 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"
