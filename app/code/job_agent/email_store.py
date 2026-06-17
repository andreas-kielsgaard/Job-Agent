from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TypeVar

from job_agent.config import ROOT
from job_agent.email_models import GmailMessageRecord, GmailSyncState, GmailThreadRecord
from job_agent.io.json_store import read_json, write_json
from job_agent.paths import runtime_dir
from job_agent.run_store import utc_now

T = TypeVar("T")


def runtime_email_dir(root: Path = ROOT) -> Path:
    return runtime_dir(root) / "email"


class GmailCredentialStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = runtime_email_dir(root) / "gmail_token.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.exists()

    def read_text(self) -> str:
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        return self.path.read_text(encoding="utf-8")

    def write_text(self, token_json: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(token_json, encoding="utf-8")

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


class GmailMessageStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = runtime_email_dir(root) / "gmail_message_index.json"
        _ensure_json(self.path, [])

    def list_all(self) -> list[GmailMessageRecord]:
        return _load_records(self.path, GmailMessageRecord)

    def upsert_many(self, messages: list[GmailMessageRecord]) -> list[GmailMessageRecord]:
        existing = {message.message_id: message for message in self.list_all()}
        now = utc_now()
        for message in messages:
            message.updated_at = now
            existing[message.message_id] = message
        records = sorted(existing.values(), key=lambda item: (item.sent_at, item.message_id), reverse=True)
        write_json(self.path, [asdict(item) for item in records])
        return records


class GmailThreadStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = runtime_email_dir(root) / "gmail_thread_cache.json"
        _ensure_json(self.path, [])

    def list_all(self) -> list[GmailThreadRecord]:
        return _load_records(self.path, GmailThreadRecord)

    def upsert_from_messages(self, messages: list[GmailMessageRecord]) -> list[GmailThreadRecord]:
        existing = {thread.thread_id: thread for thread in self.list_all()}
        grouped: dict[str, list[GmailMessageRecord]] = {}
        for message in messages:
            grouped.setdefault(message.thread_id, []).append(message)
        now = utc_now()
        for thread_id, thread_messages in grouped.items():
            thread_messages.sort(key=lambda item: (item.sent_at, item.message_id))
            latest = thread_messages[-1]
            existing[thread_id] = GmailThreadRecord(
                provider=latest.provider,
                account_id=latest.account_id,
                thread_id=thread_id,
                history_id=latest.history_id,
                subject=latest.subject,
                snippet=latest.snippet,
                message_ids=[message.message_id for message in thread_messages],
                last_message_at=latest.sent_at,
                updated_at=now,
            )
        records = sorted(existing.values(), key=lambda item: (item.last_message_at, item.thread_id), reverse=True)
        write_json(self.path, [asdict(item) for item in records])
        return [existing[thread_id] for thread_id in grouped]


class GmailSyncStateStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = runtime_email_dir(root) / "gmail_sync_state.json"
        _ensure_json(self.path, {})

    def get(self) -> GmailSyncState:
        data = read_json(self.path, {}, strict=True)
        return GmailSyncState(
            **{key: value for key, value in data.items() if key in GmailSyncState.__dataclass_fields__}
        )

    def save(self, state: GmailSyncState) -> GmailSyncState:
        write_json(self.path, asdict(state))
        return state


def _ensure_json(path: Path, default) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write_json(path, default)


def _load_records(path: Path, model: type[T]) -> list[T]:
    data = read_json(path, [], strict=True)
    if not isinstance(data, list):
        raise ValueError(f"Invalid JSON in {path}")
    allowed = set(model.__dataclass_fields__)
    return [
        model(**{key: value for key, value in item.items() if key in allowed})
        for item in data
        if isinstance(item, dict)
    ]
