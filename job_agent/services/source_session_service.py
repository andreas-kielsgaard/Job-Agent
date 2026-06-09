from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.io.yaml_store import read_yaml, write_yaml

SESSIONS_PATH = Path("sources/source-sessions.yaml")


@dataclass
class SourceSessionStatus:
    source_id: str
    status: str = "missing"
    session_scope: str = ""
    storage_state_path: str = ""
    connected_at: str = ""
    verified_at: str = ""
    expires_at: str = ""
    last_error: str = ""

    @property
    def usable(self) -> bool:
        return self.status == "connected"

    @property
    def label(self) -> str:
        return {
            "connected": "Connected",
            "expired": "Expired",
            "missing": "Not connected",
            "missing_state": "Session file missing",
            "unverified": "Not verified",
        }.get(self.status, self.status.replace("_", " ").title())

    @property
    def summary(self) -> str:
        if self.status == "connected":
            expiry = f" Expires {self.expires_at}." if self.expires_at else ""
            if self.verified_at:
                return f"Session verified for {self.session_scope or self.source_id}.{expiry}"
            return (
                f"Session connected for {self.session_scope or self.source_id}, "
                f"but not verified yet.{expiry}"
            )
        if self.status == "expired":
            return "The saved source session has expired. Connect or refresh it, then rerun the safe source test."
        if self.status == "missing_state":
            return "The saved session file is missing. Connect the source session again."
        if self.last_error:
            return self.last_error
        return "No source session is connected yet."


class SourceSessionService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.path = self.root / SESSIONS_PATH

    def status_for_source(self, source_id: str, *, session_scope: str = "") -> SourceSessionStatus:
        record = self._record(source_id)
        if not record:
            return SourceSessionStatus(source_id=source_id, session_scope=session_scope)
        status = _status_from_record(source_id, record, self.root, session_scope=session_scope)
        return status

    def record_storage_state(
        self,
        source_id: str,
        *,
        session_scope: str,
        storage_state_path: str,
        expires_at: str = "",
        verified_at: str = "",
    ) -> SourceSessionStatus:
        resolved_path = _resolve_storage_state_path(self.root, storage_state_path)
        relative_path = _relative_path(self.root, resolved_path)
        now = _now()
        data = self._data()
        sessions = data.setdefault("sources", {})
        if not isinstance(sessions, dict):
            sessions = {}
            data["sources"] = sessions
        sessions[source_id] = {
            "session_scope": session_scope,
            "storage_state_path": relative_path,
            "connected_at": now,
            "verified_at": verified_at,
            "expires_at": expires_at,
            "last_error": "",
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(self.path, data)
        return self.status_for_source(source_id, session_scope=session_scope)

    def mark_verified(self, source_id: str, *, session_scope: str = "") -> SourceSessionStatus:
        return self._update_record(
            source_id,
            session_scope=session_scope,
            verified_at=_now(),
            last_error="",
        )

    def mark_error(self, source_id: str, error: str, *, session_scope: str = "") -> SourceSessionStatus:
        return self._update_record(
            source_id,
            session_scope=session_scope,
            verified_at="",
            last_error=error.strip(),
        )

    def clear(self, source_id: str) -> None:
        data = self._data()
        sessions = data.get("sources")
        if isinstance(sessions, dict) and source_id in sessions:
            sessions.pop(source_id, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_yaml(self.path, data)

    def _record(self, source_id: str) -> dict[str, Any]:
        sessions = self._data().get("sources", {})
        if not isinstance(sessions, dict):
            return {}
        record = sessions.get(source_id)
        return record if isinstance(record, dict) else {}

    def _update_record(self, source_id: str, *, session_scope: str = "", **updates: str) -> SourceSessionStatus:
        data = self._data()
        sessions = data.setdefault("sources", {})
        if not isinstance(sessions, dict):
            sessions = {}
            data["sources"] = sessions
        record = sessions.get(source_id)
        if not isinstance(record, dict):
            record = {"session_scope": session_scope}
            sessions[source_id] = record
        for key, value in updates.items():
            record[key] = value
        if session_scope and not record.get("session_scope"):
            record["session_scope"] = session_scope
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(self.path, data)
        return self.status_for_source(source_id, session_scope=session_scope)

    def _data(self) -> dict[str, Any]:
        data = read_yaml(self.path, {"sources": {}})
        return data if isinstance(data, dict) else {"sources": {}}

def _status_from_record(
    source_id: str,
    record: dict[str, Any],
    root: Path,
    *,
    session_scope: str,
) -> SourceSessionStatus:
    state_path = str(record.get("storage_state_path") or "").strip()
    status = SourceSessionStatus(
        source_id=source_id,
        status="unverified",
        session_scope=str(record.get("session_scope") or session_scope or ""),
        storage_state_path=state_path,
        connected_at=str(record.get("connected_at") or ""),
        verified_at=str(record.get("verified_at") or ""),
        expires_at=str(record.get("expires_at") or ""),
        last_error=str(record.get("last_error") or ""),
    )
    if _is_expired(status.expires_at):
        status.status = "expired"
        return status
    if not state_path:
        status.status = "missing_state"
        return status
    if not _resolve_storage_state_path(root, state_path).exists():
        status.status = "missing_state"
        return status
    if status.last_error:
        status.status = "unverified"
        return status
    status.status = "connected"
    return status


def _resolve_storage_state_path(root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = Path(root) / path
    return path


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_expired(value: str) -> bool:
    if not value:
        return False
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
