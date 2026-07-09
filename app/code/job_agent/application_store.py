from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from .application_models import (
    APPLICATION_OUTCOMES,
    COMMUNICATION_CHANNELS,
    COMMUNICATION_DIRECTIONS,
    THREAD_LINK_STATUSES,
    ApplicationRecord,
    EmailThreadLink,
    ManualCommunicationEvent,
)
from .config import ROOT
from .io.json_store import read_json, write_json
from .paths import runtime_dir
from .run_store import utc_now

T = TypeVar("T")


def applications_dir(root: Path = ROOT) -> Path:
    return runtime_dir(root) / "applications"


class ApplicationStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = applications_dir(root) / "applications.json"
        _ensure_json_list(self.path)

    def get(self, application_id: str) -> ApplicationRecord | None:
        for record in self.list_all():
            if record.application_id == application_id or record.stable_id == application_id:
                return record
        return None

    def list_all(self) -> list[ApplicationRecord]:
        return _load_records(self.path, ApplicationRecord)

    def upsert(self, record: ApplicationRecord) -> ApplicationRecord:
        if record.outcome not in APPLICATION_OUTCOMES:
            raise ValueError(f"Unsupported application outcome: {record.outcome}")
        now = utc_now()
        existing = self.get(record.application_id)
        if existing and not record.created_at:
            record.created_at = existing.created_at
        if not record.created_at:
            record.created_at = now
        record.updated_at = now
        records = [item for item in self.list_all() if item.application_id != record.application_id]
        records.append(record)
        records.sort(key=lambda item: (item.applied_at, item.updated_at, item.title), reverse=True)
        write_json(self.path, [asdict(item) for item in records])
        return record

    def update_outcome(self, application_id: str, outcome: str) -> ApplicationRecord:
        if outcome not in APPLICATION_OUTCOMES:
            raise ValueError(f"Unsupported application outcome: {outcome}")
        record = self.get(application_id)
        if record is None:
            raise KeyError(application_id)
        record.outcome = outcome
        return self.upsert(record)


class EmailThreadLinkStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = applications_dir(root) / "email_thread_links.json"
        _ensure_json_list(self.path)

    def get(self, link_id: str) -> EmailThreadLink | None:
        for link in self.list_all():
            if link.link_id == link_id:
                return link
        return None

    def list_all(self) -> list[EmailThreadLink]:
        return _load_records(self.path, EmailThreadLink)

    def list_for_application(self, application_id: str) -> list[EmailThreadLink]:
        return [link for link in self.list_all() if link.application_id == application_id]

    def link_thread(
        self,
        application_id: str,
        thread_id: str,
        *,
        account_id: str = "",
        provider: str = "gmail",
        linked_by: str = "manual",
    ) -> EmailThreadLink:
        thread_id = thread_id.strip()
        account_id = account_id.strip()
        provider = provider.strip() or "gmail"
        linked_by = linked_by.strip() or "manual"
        if not thread_id:
            raise ValueError("Gmail thread ID is required.")
        now = utc_now()
        links = self.list_all()
        existing = next(
            (
                link
                for link in links
                if link.provider == provider and link.account_id == account_id and link.thread_id == thread_id
            ),
            None,
        )
        if existing:
            existing.application_id = application_id
            existing.status = "linked"
            existing.linked_by = linked_by
            existing.rejected_reason = ""
            existing.updated_at = now
            self._save(links)
            return existing
        link = EmailThreadLink(
            link_id=f"thread_{uuid4().hex}",
            application_id=application_id,
            provider=provider,
            account_id=account_id,
            thread_id=thread_id,
            status="linked",
            linked_by=linked_by,
            created_at=now,
            updated_at=now,
        )
        links.append(link)
        self._save(links)
        return link

    def update_status(self, link_id: str, status: str, *, rejected_reason: str = "") -> EmailThreadLink:
        if status not in THREAD_LINK_STATUSES:
            raise ValueError(f"Unsupported thread link status: {status}")
        links = self.list_all()
        for link in links:
            if link.link_id != link_id:
                continue
            link.status = status
            link.rejected_reason = rejected_reason.strip() if status == "rejected" else ""
            link.updated_at = utc_now()
            self._save(links)
            return link
        raise KeyError(link_id)

    def reassign(self, link_id: str, application_id: str) -> EmailThreadLink:
        links = self.list_all()
        for link in links:
            if link.link_id != link_id:
                continue
            link.application_id = application_id
            link.status = "linked"
            link.rejected_reason = ""
            link.updated_at = utc_now()
            self._save(links)
            return link
        raise KeyError(link_id)

    def _save(self, links: list[EmailThreadLink]) -> None:
        links.sort(key=lambda item: (item.updated_at, item.created_at, item.thread_id), reverse=True)
        write_json(self.path, [asdict(item) for item in links])


class ManualCommunicationEventStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = applications_dir(root) / "manual_events.json"
        _ensure_json_list(self.path)

    def list_all(self) -> list[ManualCommunicationEvent]:
        return _load_records(self.path, ManualCommunicationEvent)

    def list_for_application(self, application_id: str) -> list[ManualCommunicationEvent]:
        events = [event for event in self.list_all() if event.application_id == application_id]
        return sorted(events, key=lambda item: (item.occurred_at, item.created_at), reverse=True)

    def add(
        self,
        application_id: str,
        *,
        channel: str,
        direction: str,
        occurred_at: str = "",
        contact: str = "",
        subject: str = "",
        note: str = "",
    ) -> ManualCommunicationEvent:
        channel = channel.strip().lower() or "email"
        direction = direction.strip().lower() or "note"
        if channel not in COMMUNICATION_CHANNELS:
            raise ValueError(f"Unsupported communication channel: {channel}")
        if direction not in COMMUNICATION_DIRECTIONS:
            raise ValueError(f"Unsupported communication direction: {direction}")
        if not any(str(value or "").strip() for value in [contact, subject, note]):
            raise ValueError("Add a contact, subject, or note for the manual event.")
        now = utc_now()
        event = ManualCommunicationEvent(
            event_id=f"event_{uuid4().hex}",
            application_id=application_id,
            channel=channel,
            direction=direction,
            occurred_at=occurred_at.strip() or now,
            contact=contact.strip(),
            subject=subject.strip(),
            note=note.strip(),
            created_at=now,
            updated_at=now,
        )
        events = self.list_all()
        events.append(event)
        events.sort(key=lambda item: (item.occurred_at, item.created_at), reverse=True)
        write_json(self.path, [asdict(item) for item in events])
        return event


def _ensure_json_list(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write_json(path, [])


def _load_records(path: Path, model: type[T]) -> list[T]:
    data = read_json(path, [], strict=True)
    if not isinstance(data, list):
        raise ValueError(f"Invalid JSON in {path}")
    allowed = set(model.__dataclass_fields__)
    records: list[T] = []
    for item in data:
        if isinstance(item, dict):
            records.append(model(**{key: value for key, value in item.items() if key in allowed}))
    return records
