from __future__ import annotations

from dataclasses import dataclass

APPLICATION_OUTCOMES = {"open", "offered", "rejected", "closed", "archived"}
THREAD_LINK_STATUSES = {"linked", "unlinked", "rejected"}
COMMUNICATION_CHANNELS = {"email", "phone", "linkedin", "other"}
COMMUNICATION_DIRECTIONS = {"inbound", "outbound", "note"}


@dataclass
class ApplicationRecord:
    application_id: str
    stable_id: str
    fuzzy_key: str = ""
    title: str = ""
    company: str = ""
    recruiter: str = ""
    end_client: str = ""
    source: str = ""
    source_id: str = ""
    source_url: str = ""
    application_url: str = ""
    description_preview: str = ""
    applied_at: str = ""
    outcome: str = "open"
    notes: str = ""
    latest_run_id: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class EmailThreadLink:
    link_id: str
    application_id: str
    provider: str = "gmail"
    account_id: str = ""
    thread_id: str = ""
    status: str = "linked"
    linked_by: str = "manual"
    rejected_reason: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ManualCommunicationEvent:
    event_id: str
    application_id: str
    channel: str = "email"
    direction: str = "note"
    occurred_at: str = ""
    contact: str = ""
    subject: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
