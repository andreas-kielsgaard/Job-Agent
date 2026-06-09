from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from job_agent.services.source_session_service import SourceSessionService


def test_source_session_status_tracks_missing_connected_and_expired(project_root: Path) -> None:
    service = SourceSessionService(project_root)

    missing = service.status_for_source("sample-source", session_scope="example.com")
    assert missing.status == "missing"
    assert missing.usable is False

    state_path = project_root / "sources" / "sessions" / "sample-source.storage-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    connected = service.record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path=state_path.relative_to(project_root).as_posix(),
    )
    assert connected.status == "connected"
    assert connected.usable is True
    assert connected.verified_at == ""
    assert "not verified yet" in connected.summary

    verified = service.mark_verified("sample-source", session_scope="example.com")
    assert verified.status == "connected"
    assert verified.usable is True
    assert verified.verified_at
    assert "Session verified" in verified.summary

    failed = service.mark_error(
        "sample-source",
        "The page still showed a sign-in gate.",
        session_scope="example.com",
    )
    assert failed.status == "unverified"
    assert failed.usable is False
    assert "sign-in gate" in failed.summary

    refreshed = service.record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path=state_path.relative_to(project_root).as_posix(),
    )
    assert refreshed.status == "connected"
    assert refreshed.usable is True
    assert refreshed.last_error == ""

    expired_at = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    expired = service.record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path=state_path.relative_to(project_root).as_posix(),
        expires_at=expired_at,
    )
    assert expired.status == "expired"
    assert expired.usable is False
