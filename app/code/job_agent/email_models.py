from __future__ import annotations

from dataclasses import dataclass, field

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_CANDIDATE_QUERY = (
    "newer_than:180d (application OR applied OR interview OR position OR role OR opportunity OR recruiter OR hiring)"
)


@dataclass
class GmailMessageRecord:
    provider: str
    account_id: str
    message_id: str
    thread_id: str
    history_id: str = ""
    direction: str = "inbound"
    sent_at: str = ""
    from_text: str = ""
    to_text: str = ""
    subject: str = ""
    snippet: str = ""
    body_preview: str = ""
    label_ids: list[str] = field(default_factory=list)
    updated_at: str = ""


@dataclass
class GmailThreadRecord:
    provider: str
    account_id: str
    thread_id: str
    history_id: str = ""
    subject: str = ""
    snippet: str = ""
    message_ids: list[str] = field(default_factory=list)
    last_message_at: str = ""
    updated_at: str = ""


@dataclass
class GmailSyncState:
    provider: str = "gmail"
    account_id: str = ""
    connected_email: str = ""
    last_history_id: str = ""
    last_full_sync_at: str = ""
    last_sync_at: str = ""
    sync_status: str = "never"
    last_error: str = ""
    messages_indexed: int = 0
    threads_indexed: int = 0


@dataclass
class GmailSyncResult:
    status: str
    sync_type: str
    account_id: str
    query: str
    messages_considered: int = 0
    messages_fetched: int = 0
    threads_updated: int = 0
    last_history_id: str = ""
    errors: list[str] = field(default_factory=list)
