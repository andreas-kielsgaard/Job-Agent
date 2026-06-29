from __future__ import annotations

from pathlib import Path

from job_agent.application_models import ApplicationRecord
from job_agent.application_store import ApplicationStore, EmailThreadLinkStore
from job_agent.email_models import GmailMessageRecord
from job_agent.email_store import GmailMessageStore, GmailThreadStore
from job_agent.services.gmail_application_match_service import GmailApplicationMatchService


def test_gmail_application_matcher_auto_links_high_confidence_confirmation(project_root: Path) -> None:
    ApplicationStore(project_root).upsert(
        ApplicationRecord(
            application_id="dice-app",
            stable_id="dice-app",
            title="Urgent Need for SAP ABAP(Only need EX T-Mobile experienced Candidates) No H1b",
            source="Dice",
            source_url="https://www.dice.com/job-detail/8cc3d6f0-5e55-443a-ad14-502f02ad9e3c",
            applied_at="2026-06-19T09:18:17+00:00",
        )
    )
    _seed_message(
        project_root,
        thread_id="dice-thread",
        sent_at="2026-06-19T09:24:01+00:00",
        from_text="applyonline@dice.com",
        subject=(
            "Application for Urgent Need for SAP ABAP(Only need EX T-Mobile experienced Candidates) "
            "No H1b at Drunix Solution Inc sent"
        ),
        body="Your application was successfully submitted.",
    )

    result = GmailApplicationMatchService(project_root).match_cached_threads()

    links = EmailThreadLinkStore(project_root).list_for_application("dice-app")
    assert result.linked_count == 1
    assert links[0].thread_id == "dice-thread"
    assert links[0].linked_by == "auto"


def test_gmail_application_matcher_links_source_follow_up_near_application_date(project_root: Path) -> None:
    ApplicationStore(project_root).upsert(
        ApplicationRecord(
            application_id="energize-app",
            stable_id="energize-app",
            title="SAP ABAP Developer",
            source="Energize Recruitment",
            source_url="https://www.energizerecruitment.com/jobs/view/sap-abap-developer-35267",
            applied_at="2026-06-18T09:01:21+00:00",
        )
    )
    _seed_message(
        project_root,
        thread_id="energize-follow-up",
        sent_at="2026-06-18T09:33:50+00:00",
        from_text="Energize Group <noreply@energizerec.com>",
        subject="Andreas, we'd love to learn more about you.",
        body="Dear Andreas, thank you for your recent application. Please take our short questionnaire.",
    )

    result = GmailApplicationMatchService(project_root).match_cached_threads()

    links = EmailThreadLinkStore(project_root).list_for_application("energize-app")
    assert result.linked_count == 1
    assert links[0].thread_id == "energize-follow-up"


def test_gmail_application_matcher_does_not_auto_link_title_only_company_mismatch(project_root: Path) -> None:
    ApplicationStore(project_root).upsert(
        ApplicationRecord(
            application_id="olik-app",
            stable_id="olik-app",
            title="Sr SAP Developer",
            source="LinkedIn Jobs (SAP ABAP/RAP)",
            source_url="https://www.linkedin.com/jobs/view/sr-sap-developer-at-olik-global-4429103764",
            applied_at="2026-06-16T13:01:47+00:00",
        )
    )
    _seed_message(
        project_root,
        thread_id="qode-thread",
        sent_at="2026-06-16T13:02:46+00:00",
        from_text="Workable <noreply@candidates.workablemail.com>",
        subject="Thanks for applying to Qode",
        body="Your application for the Sr SAP Developer job was submitted successfully.",
    )

    result = GmailApplicationMatchService(project_root).match_cached_threads()

    assert result.linked_count == 0
    assert EmailThreadLinkStore(project_root).list_for_application("olik-app") == []


def test_gmail_application_matcher_respects_rejected_existing_thread_link(project_root: Path) -> None:
    ApplicationStore(project_root).upsert(
        ApplicationRecord(
            application_id="whitehall-app",
            stable_id="whitehall-app",
            title="SAP ABAP Senior Developer",
            source="Whitehall Resources SAP Jobs",
            applied_at="2026-06-16T12:34:57+00:00",
        )
    )
    _seed_message(
        project_root,
        thread_id="whitehall-thread",
        sent_at="2026-06-16T12:39:37+00:00",
        from_text="Charlie Regan <noreply@broadbean.net>",
        subject="Whitehall Resources - BBBH64648 SAP ABAP Senior Developer",
        body="Thank you for your application for the role of The SAP ABAP Senior Developer BBBH64648.",
    )
    links = EmailThreadLinkStore(project_root)
    rejected = links.link_thread("whitehall-app", "whitehall-thread", account_id="me@example.com")
    links.update_status(rejected.link_id, "rejected", rejected_reason="Wrong role")

    result = GmailApplicationMatchService(project_root).match_cached_threads()

    assert result.linked_count == 0
    assert links.get(rejected.link_id).status == "rejected"


def _seed_message(
    root: Path,
    *,
    thread_id: str,
    sent_at: str,
    from_text: str,
    subject: str,
    body: str,
) -> None:
    message = GmailMessageRecord(
        provider="gmail",
        account_id="me@example.com",
        message_id=f"{thread_id}-m1",
        thread_id=thread_id,
        direction="inbound",
        sent_at=sent_at,
        from_text=from_text,
        to_text="Andreas Kielsgaard <andreas.kielsgaard@gmail.com>",
        subject=subject,
        snippet=body,
        body_preview=body,
    )
    GmailMessageStore(root).upsert_many([message])
    GmailThreadStore(root).upsert_from_messages([message])
