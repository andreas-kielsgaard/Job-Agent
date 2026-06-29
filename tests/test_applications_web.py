from __future__ import annotations

from fastapi.testclient import TestClient
from tests.helpers import write_sample_package

from job_agent.application_models import ApplicationRecord
from job_agent.application_store import ApplicationStore, EmailThreadLinkStore
from job_agent.email_models import GmailMessageRecord
from job_agent.email_store import GmailMessageStore, GmailThreadStore


def test_marking_job_applied_creates_application_pages(client: TestClient, project_root) -> None:
    write_sample_package(project_root, stable_id="stable-1", title="SAP ABAP Consultant")

    response = client.post("/api/jobs/status", json={"job_ids": ["stable-1"], "status": "applied"})

    assert response.status_code == 200
    assert ApplicationStore(project_root).get("stable-1") is not None
    overview = client.get("/applications")
    assert overview.status_code == 200
    assert "SAP ABAP Consultant" in overview.text
    assert "/applications/stable-1" in overview.text
    detail = client.get("/applications/stable-1")
    assert detail.status_code == 200
    assert "Communication" in detail.text
    assert "Manual Links" in detail.text
    job_detail = client.get("/jobs/stable-1?run_id=run-1")
    assert 'href="/applications/stable-1"' in job_detail.text


def test_application_outcome_is_independent_from_job_status(client: TestClient, project_root) -> None:
    write_sample_package(project_root, stable_id="stable-1")
    client.post("/api/jobs/status", json={"job_ids": ["stable-1"], "status": "applied"})

    response = client.post(
        "/api/applications/stable-1/outcome",
        data={"outcome": "offered"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert ApplicationStore(project_root).get("stable-1").outcome == "offered"
    jobs = client.get("/jobs?app_status=applied&dedupe=0")
    assert 'data-current-status="applied"' in jobs.text


def test_manual_thread_link_event_reject_and_reassign(client: TestClient, project_root) -> None:
    write_sample_package(project_root, stable_id="stable-1", title="First Applied Role")
    write_sample_package(project_root, stable_id="stable-2", title="Second Applied Role")
    client.post("/api/jobs/status", json={"job_ids": ["stable-1", "stable-2"], "status": "applied"})

    link_response = client.post(
        "/api/applications/stable-1/gmail-thread/manual-link",
        data={"thread_id": "gmail-thread-1", "account_id": "me@example.com"},
        follow_redirects=False,
    )
    event_response = client.post(
        "/api/applications/stable-1/manual-event",
        data={
            "channel": "email",
            "direction": "inbound",
            "occurred_at": "2026-06-15T09:30",
            "contact": "recruiter@example.com",
            "subject": "Interview slot",
            "note": "Recruiter proposed Tuesday.",
        },
        follow_redirects=False,
    )

    assert link_response.status_code == 303
    assert event_response.status_code == 303
    overview = client.get("/applications")
    assert "Interview slot" in overview.text
    assert "linked thread" in overview.text

    link = EmailThreadLinkStore(project_root).list_for_application("stable-1")[0]
    reject_response = client.post(
        f"/api/applications/stable-1/gmail-thread/{link.link_id}/reject",
        data={"rejected_reason": "Wrong role"},
        follow_redirects=False,
    )
    rejected_detail = client.get("/applications/stable-1")
    reassign_response = client.post(
        f"/api/applications/stable-1/gmail-thread/{link.link_id}/reassign",
        data={"target_application_id": "stable-2"},
        follow_redirects=False,
    )

    assert reject_response.status_code == 303
    assert "Wrong role" in rejected_detail.text
    assert reassign_response.status_code == 303
    assert EmailThreadLinkStore(project_root).list_for_application("stable-1") == []
    assert EmailThreadLinkStore(project_root).list_for_application("stable-2")[0].thread_id == "gmail-thread-1"


def test_application_pages_render_synced_gmail_thread_cache(client: TestClient, project_root) -> None:
    write_sample_package(project_root, stable_id="stable-1", title="SAP ABAP Consultant")
    client.post("/api/jobs/status", json={"job_ids": ["stable-1"], "status": "applied"})
    client.post(
        "/api/applications/stable-1/gmail-thread/manual-link",
        data={"thread_id": "gmail-thread-1", "account_id": "me@example.com"},
        follow_redirects=False,
    )
    messages = [
        GmailMessageRecord(
            provider="gmail",
            account_id="me@example.com",
            message_id="m1",
            thread_id="gmail-thread-1",
            direction="outbound",
            sent_at="2026-06-15T09:30:00+00:00",
            to_text="recruiter@example.com",
            subject="Application sent",
            snippet="I applied for the role.",
        ),
        GmailMessageRecord(
            provider="gmail",
            account_id="me@example.com",
            message_id="m2",
            thread_id="gmail-thread-1",
            direction="inbound",
            sent_at="2026-06-16T10:00:00+00:00",
            from_text="recruiter@example.com",
            subject="Interview confirmed",
            snippet="Thanks, Tuesday is confirmed.",
        ),
    ]
    GmailMessageStore(project_root).upsert_many(messages)
    GmailThreadStore(project_root).upsert_from_messages(messages)

    overview = client.get("/applications")
    detail = client.get("/applications/stable-1")

    assert "Interview confirmed" in overview.text
    assert "Thanks, Tuesday is confirmed." in overview.text
    assert "Application sent" in detail.text
    assert "2 cached messages" in detail.text


def test_match_cached_gmail_threads_endpoint_auto_links_confirmation(client: TestClient, project_root) -> None:
    ApplicationStore(project_root).upsert(
        ApplicationRecord(
            application_id="whitehall-app",
            stable_id="whitehall-app",
            title="SAP ABAP Senior Developer",
            source="Whitehall Resources SAP Jobs",
            applied_at="2026-06-16T12:34:57+00:00",
        )
    )
    messages = [
        GmailMessageRecord(
            provider="gmail",
            account_id="me@example.com",
            message_id="m1",
            thread_id="whitehall-thread",
            direction="inbound",
            sent_at="2026-06-16T12:39:37+00:00",
            from_text="Charlie Regan <noreply@broadbean.net>",
            to_text="Andreas Kielsgaard <andreas.kielsgaard@gmail.com>",
            subject="Whitehall Resources - BBBH64648 SAP ABAP Senior Developer",
            snippet="Thank you for your application for the role of The SAP ABAP Senior Developer BBBH64648.",
            body_preview="Thank you for your application for the role of The SAP ABAP Senior Developer BBBH64648.",
        )
    ]
    GmailMessageStore(project_root).upsert_many(messages)
    GmailThreadStore(project_root).upsert_from_messages(messages)

    response = client.post("/api/applications/gmail-match", follow_redirects=False)

    assert response.status_code == 303
    assert "auto-linked+1" in response.headers["location"]
    link = EmailThreadLinkStore(project_root).list_for_application("whitehall-app")[0]
    assert link.thread_id == "whitehall-thread"
    assert link.linked_by == "auto"
