from __future__ import annotations

from pathlib import Path
from typing import Any

from job_agent.application_store import ApplicationStore, EmailThreadLinkStore, ManualCommunicationEventStore
from job_agent.config import ROOT
from job_agent.services.application_tracker_service import ApplicationTrackerService
from job_agent.services.email_sync_service import EmailSyncService
from job_agent.web.view_models.applications import build_application_detail_view, build_applications_view


class ApplicationWorkflowHandler:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.applications = ApplicationStore(self.root)
        self.thread_links = EmailThreadLinkStore(self.root)
        self.manual_events = ManualCommunicationEventStore(self.root)
        self.tracker = ApplicationTrackerService(self.root)
        self.email_sync = EmailSyncService(self.root)

    def list_view(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        self.tracker.backfill_applied()
        view = build_applications_view(filters, self.root)
        view["gmail"] = self.email_sync.status()
        return view

    def detail_view(self, application_id: str) -> dict[str, Any]:
        self.tracker.backfill_applied()
        return build_application_detail_view(application_id, self.root)

    def update_outcome(self, application_id: str, outcome: str):
        self.require_application(application_id)
        return self.applications.update_outcome(application_id, outcome)

    def link_gmail_thread(self, application_id: str, thread_id: str, account_id: str = ""):
        self.require_application(application_id)
        return self.thread_links.link_thread(application_id, thread_id, account_id=account_id, provider="gmail")

    def unlink_thread(self, application_id: str, link_id: str):
        self.require_thread_for_application(application_id, link_id)
        return self.thread_links.update_status(link_id, "unlinked")

    def reject_thread(self, application_id: str, link_id: str, reason: str = ""):
        self.require_thread_for_application(application_id, link_id)
        return self.thread_links.update_status(link_id, "rejected", rejected_reason=reason)

    def reassign_thread(self, application_id: str, link_id: str, target_application_id: str):
        self.require_thread_for_application(application_id, link_id)
        self.require_application(target_application_id)
        return self.thread_links.reassign(link_id, target_application_id)

    def add_manual_event(
        self,
        application_id: str,
        *,
        channel: str,
        direction: str,
        occurred_at: str = "",
        contact: str = "",
        subject: str = "",
        note: str = "",
    ):
        self.require_application(application_id)
        return self.manual_events.add(
            application_id,
            channel=channel,
            direction=direction,
            occurred_at=occurred_at,
            contact=contact,
            subject=subject,
            note=note,
        )

    def sync_gmail(self, *, max_messages: int = 100, force_full: bool = False):
        return self.email_sync.sync_recent_candidates(max_messages=max_messages, force_full=force_full)

    def require_application(self, application_id: str):
        self.tracker.backfill_applied()
        record = self.applications.get(application_id)
        if record is None:
            raise KeyError(application_id)
        return record

    def require_thread_for_application(self, application_id: str, link_id: str):
        link = self.thread_links.get(link_id)
        if link is None or link.application_id != application_id:
            raise KeyError(link_id)
        return link
