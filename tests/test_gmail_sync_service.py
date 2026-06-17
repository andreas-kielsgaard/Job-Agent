from __future__ import annotations

import tempfile
from pathlib import Path

from job_agent.email_models import GmailMessageRecord
from job_agent.email_store import GmailMessageStore, GmailSyncStateStore, GmailThreadStore
from job_agent.services.email_provider import GmailHistoryStaleError
from job_agent.services.email_sync_service import EmailSyncService


class FakeGmailProvider:
    def __init__(self, *, stale_history: bool = False) -> None:
        self.stale_history = stale_history
        self.candidate_queries: list[str] = []
        self.history_starts: list[str] = []

    def profile(self) -> dict[str, str]:
        return {"emailAddress": "me@example.com", "historyId": "200"}

    def list_candidate_message_ids(self, query: str, *, max_results: int) -> list[str]:
        self.candidate_queries.append(query)
        return ["m1", "m2"][:max_results]

    def list_history_message_ids(self, start_history_id: str, *, max_results: int) -> tuple[list[str], str]:
        self.history_starts.append(start_history_id)
        if self.stale_history:
            raise GmailHistoryStaleError("stale")
        return ["m3"][:max_results], "250"

    def get_message(self, message_id: str) -> GmailMessageRecord:
        records = {
            "m1": GmailMessageRecord(
                provider="gmail",
                account_id="me@example.com",
                message_id="m1",
                thread_id="t1",
                history_id="101",
                direction="inbound",
                sent_at="2026-06-15T09:30:00+00:00",
                from_text="recruiter@example.com",
                to_text="me@example.com",
                subject="Application received",
                snippet="Thanks for applying.",
            ),
            "m2": GmailMessageRecord(
                provider="gmail",
                account_id="me@example.com",
                message_id="m2",
                thread_id="t2",
                history_id="102",
                direction="outbound",
                sent_at="2026-06-16T09:30:00+00:00",
                from_text="me@example.com",
                to_text="recruiter@example.com",
                subject="Interview availability",
                snippet="Tuesday works.",
                label_ids=["SENT"],
            ),
            "m3": GmailMessageRecord(
                provider="gmail",
                account_id="me@example.com",
                message_id="m3",
                thread_id="t1",
                history_id="251",
                direction="inbound",
                sent_at="2026-06-17T09:30:00+00:00",
                from_text="recruiter@example.com",
                to_text="me@example.com",
                subject="Interview availability",
                snippet="Confirmed.",
            ),
        }
        return records[message_id]


def test_gmail_sync_full_sync_stores_messages_threads_and_state() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        provider = FakeGmailProvider()

        result = EmailSyncService(root, provider).sync_recent_candidates(max_messages=10)

        assert result.status == "completed"
        assert result.sync_type == "full"
        assert result.messages_fetched == 2
        assert result.threads_updated == 2
        messages = GmailMessageStore(root).list_all()
        threads = GmailThreadStore(root).list_all()
        state = GmailSyncStateStore(root).get()
        assert {message.message_id for message in messages} == {"m1", "m2"}
        assert {thread.thread_id for thread in threads} == {"t1", "t2"}
        assert state.account_id == "me@example.com"
        assert state.last_history_id == "200"
        assert state.messages_indexed == 2


def test_gmail_sync_uses_history_after_initial_sync() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        state = GmailSyncStateStore(root).get()
        state.account_id = "me@example.com"
        state.connected_email = "me@example.com"
        state.last_history_id = "200"
        GmailSyncStateStore(root).save(state)
        provider = FakeGmailProvider()

        result = EmailSyncService(root, provider).sync_recent_candidates(max_messages=10)

        assert result.sync_type == "partial"
        assert provider.history_starts == ["200"]
        assert GmailMessageStore(root).list_all()[0].message_id == "m3"
        assert GmailSyncStateStore(root).get().last_history_id == "251"


def test_gmail_sync_falls_back_to_full_when_history_is_stale() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        state = GmailSyncStateStore(root).get()
        state.account_id = "me@example.com"
        state.connected_email = "me@example.com"
        state.last_history_id = "old"
        GmailSyncStateStore(root).save(state)
        provider = FakeGmailProvider(stale_history=True)

        result = EmailSyncService(root, provider).sync_recent_candidates(max_messages=10)

        assert result.sync_type == "full"
        assert provider.history_starts == ["old"]
        assert provider.candidate_queries
