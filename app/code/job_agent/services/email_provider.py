from __future__ import annotations

from typing import Protocol

from job_agent.email_models import GmailMessageRecord


class GmailHistoryStaleError(RuntimeError):
    pass


class EmailProvider(Protocol):
    def profile(self) -> dict[str, str]:
        raise NotImplementedError

    def list_candidate_message_ids(self, query: str, *, max_results: int) -> list[str]:
        raise NotImplementedError

    def list_history_message_ids(self, start_history_id: str, *, max_results: int) -> tuple[list[str], str]:
        raise NotImplementedError

    def get_message(self, message_id: str) -> GmailMessageRecord:
        raise NotImplementedError
