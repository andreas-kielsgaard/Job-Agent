from __future__ import annotations

import tempfile
from pathlib import Path

from tests.helpers import write_sample_package

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.application_store import ApplicationStore, EmailThreadLinkStore, ManualCommunicationEventStore
from job_agent.services.application_index_service import ApplicationIndexService
from job_agent.services.application_tracker_service import ApplicationTrackerService


def test_application_store_preserves_user_outcome_on_upsert() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_sample_package(root, stable_id="stable-1", title="SAP ABAP Consultant")
        status_store = ApplicationStatusStore(root)
        status_store.ensure_for_job(
            stable_id="stable-1",
            fuzzy_key="fuzzy-1",
            title="SAP ABAP Consultant",
            company="Recruiter",
            source="Sample",
            url="https://example.com/stable-1",
            application_url="https://example.com/stable-1/apply",
        )
        status_store.update_status("stable-1", "applied")
        tracker = ApplicationTrackerService(root)

        created = tracker.ensure_from_job("stable-1")
        ApplicationStore(root).update_outcome("stable-1", "offered")
        refreshed = tracker.ensure_from_job("stable-1")

        assert created.application_id == "stable-1"
        assert refreshed.outcome == "offered"
        assert refreshed.title == "SAP ABAP Consultant"


def test_tracker_backfills_applied_package_without_status_record() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        paths = write_sample_package(root, stable_id="stable-1", title="Applied Package")
        package_index = Path(paths["index"])
        text = package_index.read_text(encoding="utf-8")
        package_index.write_text(text.replace('"application_status": "unreviewed"', '"application_status": "applied"'))

        records = ApplicationTrackerService(root).backfill_applied()

        assert [record.application_id for record in records] == ["stable-1"]
        assert ApplicationStore(root).get("stable-1").title == "Applied Package"


def test_thread_links_and_manual_events_feed_application_index() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_sample_package(root, stable_id="stable-1")
        status_store = ApplicationStatusStore(root)
        status_store.ensure_for_job(
            stable_id="stable-1",
            fuzzy_key="fuzzy-1",
            title="SAP ABAP Consultant",
            company="Recruiter",
            source="Sample",
            url="https://example.com/stable-1",
            application_url="https://example.com/stable-1/apply",
        )
        status_store.update_status("stable-1", "applied")
        ApplicationTrackerService(root).ensure_from_job("stable-1")
        links = EmailThreadLinkStore(root)
        events = ManualCommunicationEventStore(root)

        link = links.link_thread("stable-1", "gmail-thread-1", account_id="me@example.com")
        events.add(
            "stable-1",
            channel="email",
            direction="inbound",
            occurred_at="2026-06-15T09:30",
            contact="recruiter@example.com",
            subject="Interview slot",
            note="Recruiter proposed Tuesday.",
        )
        links.update_status(link.link_id, "unlinked")
        links.reassign(link.link_id, "stable-1")
        rows = ApplicationIndexService(root).list_rows()

        assert rows[0]["communication_state"] == "linked"
        assert rows[0]["linked_thread_count"] == 1
        assert rows[0]["preview_events"][0]["summary"] == "Interview slot"
        assert links.get(link.link_id).status == "linked"


def test_thread_link_rejects_and_reassigns() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        links = EmailThreadLinkStore(root)

        link = links.link_thread("app-1", "thread-1")
        rejected = links.update_status(link.link_id, "rejected", rejected_reason="Wrong role")
        reassigned = links.reassign(link.link_id, "app-2")

        assert rejected.status == "rejected"
        assert rejected.rejected_reason == "Wrong role"
        assert reassigned.application_id == "app-2"
        assert reassigned.status == "linked"
