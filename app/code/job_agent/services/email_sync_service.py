from __future__ import annotations

from pathlib import Path

from job_agent.config import ROOT
from job_agent.email_models import GMAIL_CANDIDATE_QUERY, GmailMessageRecord, GmailSyncResult, GmailSyncState
from job_agent.email_store import GmailMessageStore, GmailSyncStateStore, GmailThreadStore
from job_agent.run_store import utc_now
from job_agent.services.connector_settings_service import ConnectorSettingsService
from job_agent.services.email_provider import EmailProvider, GmailHistoryStaleError
from job_agent.services.gmail_email_provider import GmailEmailProvider


class EmailSyncService:
    def __init__(self, root: Path = ROOT, provider: EmailProvider | None = None) -> None:
        self.root = Path(root)
        self.provider = provider
        self.messages = GmailMessageStore(self.root)
        self.threads = GmailThreadStore(self.root)
        self.state_store = GmailSyncStateStore(self.root)

    def status(self) -> dict[str, object]:
        email = ConnectorSettingsService(self.root).load()["email"]
        state = self.state_store.get()
        return {
            "connected": email.get("oauth_status") == "connected",
            "oauth_ready": bool(email.get("oauth_ready")),
            "account_email": email.get("connected_email") or email.get("account_email") or state.connected_email,
            "last_sync_at": state.last_sync_at,
            "last_full_sync_at": state.last_full_sync_at,
            "last_error": state.last_error,
            "messages_indexed": state.messages_indexed,
            "threads_indexed": state.threads_indexed,
            "sync_status": state.sync_status,
        }

    def sync_recent_candidates(
        self,
        *,
        max_messages: int = 100,
        force_full: bool = False,
        query: str = GMAIL_CANDIDATE_QUERY,
    ) -> GmailSyncResult:
        max_messages = max(1, min(max_messages, 500))
        provider = self.provider or GmailEmailProvider.from_credentials_file(self.root)
        profile = provider.profile()
        account_id = str(profile.get("emailAddress") or "")
        state = self.state_store.get()
        if account_id:
            state.account_id = account_id
            state.connected_email = account_id

        sync_type = "full"
        message_ids: list[str]
        last_history_id = state.last_history_id
        if state.last_history_id and not force_full:
            try:
                message_ids, last_history_id = provider.list_history_message_ids(
                    state.last_history_id,
                    max_results=max_messages,
                )
                sync_type = "partial"
            except GmailHistoryStaleError:
                message_ids = provider.list_candidate_message_ids(query, max_results=max_messages)
                last_history_id = str(profile.get("historyId") or state.last_history_id)
                sync_type = "full"
        else:
            message_ids = provider.list_candidate_message_ids(query, max_results=max_messages)
            last_history_id = str(profile.get("historyId") or state.last_history_id)

        message_ids = _dedupe(message_ids)[:max_messages]
        fetched = [provider.get_message(message_id) for message_id in message_ids]
        for message in fetched:
            if not message.account_id:
                message.account_id = account_id
        all_messages = self.messages.upsert_many(fetched)
        updated_threads = self.threads.upsert_from_messages(fetched)
        state = self._updated_state(
            state,
            account_id=account_id,
            last_history_id=_latest_history_id(last_history_id, fetched),
            sync_type=sync_type,
            all_messages=all_messages,
        )
        self.state_store.save(state)
        return GmailSyncResult(
            status="completed",
            sync_type=sync_type,
            account_id=account_id,
            query=query,
            messages_considered=len(message_ids),
            messages_fetched=len(fetched),
            threads_updated=len(updated_threads),
            last_history_id=state.last_history_id,
        )

    def _updated_state(
        self,
        state: GmailSyncState,
        *,
        account_id: str,
        last_history_id: str,
        sync_type: str,
        all_messages: list[GmailMessageRecord],
    ) -> GmailSyncState:
        now = utc_now()
        threads_indexed = len({message.thread_id for message in all_messages if message.thread_id})
        state.account_id = account_id
        state.connected_email = account_id
        state.last_history_id = last_history_id
        state.last_sync_at = now
        if sync_type == "full":
            state.last_full_sync_at = now
        state.sync_status = "completed"
        state.last_error = ""
        state.messages_indexed = len(all_messages)
        state.threads_indexed = threads_indexed
        return state


def _latest_history_id(current: str, messages: list[GmailMessageRecord]) -> str:
    candidates = [current, *[message.history_id for message in messages]]
    numeric = [int(value) for value in candidates if str(value).isdigit()]
    return str(max(numeric)) if numeric else next((value for value in candidates if value), "")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result
