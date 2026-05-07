from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from job_agent.io.json_store import read_json, write_json
from job_agent.models import Job
from job_agent.store import JobStore


def test_new_seen_changed_and_filter_new(project_root: Path) -> None:
    store = JobStore(project_root)
    job = _job(description="ABAP OData")

    first = store.classify([job], today=date(2026, 5, 6))[0]
    assert first.status == "new"
    store.mark_seen([first])

    same = store.classify([job], today=date(2026, 5, 7))[0]
    assert same.status == "previously_seen"

    changed_job = _job(description="ABAP OData RAP")
    changed = store.classify([changed_job], today=date(2026, 5, 7))[0]
    assert changed.status == "changed"
    assert store.filter_new([job, changed_job]) == [changed_job]


def test_mark_seen_persists_expected_fields(project_root: Path) -> None:
    store = JobStore(project_root)
    state = store.classify([_job()], today=date(2026, 5, 6))[0]

    store.mark_seen([state])

    records = read_json(project_root / "jobs" / "seen_jobs.json", [], strict=True)
    assert records[0]["stable_id"] == state.stable_id
    assert records[0]["fuzzy_key"] == state.fuzzy_key
    assert records[0]["first_seen_date"] == "2026-05-06"
    assert records[0]["last_seen_date"]
    assert records[0]["content_hash"] == state.content_hash


def test_fuzzy_key_matches_similar_repost_with_different_url(project_root: Path) -> None:
    store = JobStore(project_root)
    original = _job(url="https://example.com/a", description="ABAP OData RAP integration")
    state = store.classify([original], today=date(2026, 5, 6))[0]
    store.mark_seen([state])

    repost = _job(url="https://example.com/b", description="ABAP OData RAP integration")
    repost_state = store.classify([repost], today=date(2026, 5, 7))[0]

    assert repost_state.status == "previously_seen"
    assert repost_state.stable_id != state.stable_id
    assert repost_state.fuzzy_key == state.fuzzy_key


def test_legacy_string_list_migration_and_corrupt_json(project_root: Path) -> None:
    path = project_root / "jobs" / "seen_jobs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, ["abc123"])

    records = JobStore(project_root)._load_records()
    assert records[0].stable_id == "abc123"
    assert records[0].status == "previously_seen"

    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match=re.escape(str(path))):
        JobStore(project_root).classify([_job()])


def _job(url: str = "https://example.com/job", description: str = "ABAP OData") -> Job:
    return Job(
        title="SAP ABAP Consultant",
        company="Recruiter",
        location="Copenhagen",
        start_date="June 2026",
        url=url,
        description=description,
    )
