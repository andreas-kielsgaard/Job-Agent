from __future__ import annotations

from fastapi.testclient import TestClient
from tests.helpers import write_sample_package

from job_agent.application_store import ApplicationStore, EmailThreadLinkStore


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
