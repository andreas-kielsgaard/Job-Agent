from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .config import ROOT
from .io.json_store import read_json, write_json
from .run_store import utc_now

APPLICATION_STATUSES = {"unreviewed", "interesting", "not_interesting", "applied", "archived"}


@dataclass
class ApplicationStatusRecord:
    stable_id: str
    fuzzy_key: str
    title: str
    company: str
    source: str
    url: str
    application_url: str
    status: str = "unreviewed"
    status_updated_at: str = ""
    notes: str = ""
    applied_at: str = ""
    not_interesting_reason: str = ""


class ApplicationStatusStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = root / "jobs" / "application_status.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            write_json(self.path, [])

    def ensure_for_job(
        self, *, stable_id: str, fuzzy_key: str, title: str, company: str, source: str, url: str, application_url: str
    ) -> ApplicationStatusRecord:
        existing = self.get(stable_id)
        if existing:
            return existing
        record = ApplicationStatusRecord(
            stable_id=stable_id,
            fuzzy_key=fuzzy_key,
            title=title,
            company=company,
            source=source,
            url=url,
            application_url=application_url,
            status_updated_at=utc_now(),
        )
        self.upsert(record)
        return record

    def update_status(
        self,
        stable_id: str,
        status: str,
        notes: str | None = None,
        not_interesting_reason: str | None = None,
    ) -> ApplicationStatusRecord:
        if status not in APPLICATION_STATUSES:
            raise ValueError(f"Unsupported application status: {status}")
        record = self.get(stable_id)
        if record is None:
            raise KeyError(f"Unknown stable_id: {stable_id}")
        record.status = status
        record.status_updated_at = utc_now()
        if notes is not None:
            record.notes = notes
        if not_interesting_reason is not None:
            record.not_interesting_reason = not_interesting_reason
        if status == "applied" and not record.applied_at:
            record.applied_at = utc_now()
        self.upsert(record)
        return record

    def get(self, stable_id: str) -> ApplicationStatusRecord | None:
        for record in self.list_all():
            if record.stable_id == stable_id:
                return record
        return None

    def list_all(self) -> list[ApplicationStatusRecord]:
        data = read_json(self.path, [], strict=True)
        return [ApplicationStatusRecord(**item) for item in data]

    def upsert(self, record: ApplicationStatusRecord) -> None:
        records = [item for item in self.list_all() if item.stable_id != record.stable_id]
        records.append(record)
        records.sort(key=lambda item: item.status_updated_at, reverse=True)
        write_json(self.path, [asdict(item) for item in records])
