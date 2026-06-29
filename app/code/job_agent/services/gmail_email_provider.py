from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.email_models import GMAIL_READONLY_SCOPE, GmailMessageRecord
from job_agent.email_store import GmailCredentialStore
from job_agent.services.email_provider import GmailHistoryStaleError

GMAIL_METADATA_HEADERS = ("From", "To", "Cc", "Subject", "Date")


@dataclass
class GmailOAuthResult:
    credentials_json: str
    account_email: str
    history_id: str = ""


class GmailOAuthClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.code_verifier = ""

    def authorization_url(self, state: str) -> str:
        Flow = _google_flow()
        flow = Flow.from_client_config(_client_config(self.config), scopes=list(self.config["scopes"]))
        flow.redirect_uri = self.config["redirect_uri"]
        authorization_url, _state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        self.code_verifier = str(flow.code_verifier or "")
        return str(authorization_url)

    def complete_oauth(self, code: str, state: str, *, code_verifier: str = "") -> GmailOAuthResult:
        Flow = _google_flow()
        flow = Flow.from_client_config(
            _client_config(self.config),
            scopes=list(self.config["scopes"]),
            state=state,
            code_verifier=code_verifier,
        )
        flow.redirect_uri = self.config["redirect_uri"]
        flow.fetch_token(code=code)
        credentials = flow.credentials
        service = _build_service(credentials)
        profile = service.users().getProfile(userId="me").execute()
        return GmailOAuthResult(
            credentials_json=credentials.to_json(),
            account_email=str(profile.get("emailAddress") or ""),
            history_id=str(profile.get("historyId") or ""),
        )


class GmailEmailProvider:
    def __init__(self, service: Any, account_id: str = "") -> None:
        self.service = service
        self.account_id = account_id

    @classmethod
    def from_credentials_file(cls, root: Path = ROOT) -> GmailEmailProvider:
        Credentials, Request = _google_credentials()
        store = GmailCredentialStore(root)
        token = json.loads(store.read_text())
        credentials = Credentials.from_authorized_user_info(token, [GMAIL_READONLY_SCOPE])
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            store.write_text(credentials.to_json())
        if not credentials.valid:
            raise ValueError("Saved Gmail credentials are no longer valid. Reconnect Gmail.")
        service = _build_service(credentials)
        profile = service.users().getProfile(userId="me").execute()
        return cls(service, str(profile.get("emailAddress") or ""))

    def profile(self) -> dict[str, str]:
        profile = self.service.users().getProfile(userId="me").execute()
        self.account_id = str(profile.get("emailAddress") or self.account_id)
        return {
            "emailAddress": self.account_id,
            "historyId": str(profile.get("historyId") or ""),
            "messagesTotal": str(profile.get("messagesTotal") or ""),
            "threadsTotal": str(profile.get("threadsTotal") or ""),
        }

    def list_candidate_message_ids(self, query: str, *, max_results: int) -> list[str]:
        result: list[str] = []
        page_token = ""
        while len(result) < max_results:
            request = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=min(100, max_results - len(result)),
                    pageToken=page_token or None,
                )
            )
            response = request.execute()
            result.extend(str(item.get("id")) for item in response.get("messages", []) if item.get("id"))
            page_token = str(response.get("nextPageToken") or "")
            if not page_token:
                break
        return result

    def list_history_message_ids(self, start_history_id: str, *, max_results: int) -> tuple[list[str], str]:
        HttpError = _google_http_error()
        result: list[str] = []
        page_token = ""
        latest_history_id = start_history_id
        try:
            while len(result) < max_results:
                request = (
                    self.service.users()
                    .history()
                    .list(
                        userId="me",
                        startHistoryId=start_history_id,
                        historyTypes=["messageAdded"],
                        maxResults=min(100, max_results - len(result)),
                        pageToken=page_token or None,
                    )
                )
                response = request.execute()
                latest_history_id = str(response.get("historyId") or latest_history_id)
                for history in response.get("history", []):
                    for added in history.get("messagesAdded", []):
                        message_id = str(added.get("message", {}).get("id") or "")
                        if message_id:
                            result.append(message_id)
                page_token = str(response.get("nextPageToken") or "")
                if not page_token:
                    break
        except HttpError as exc:
            if getattr(getattr(exc, "resp", None), "status", None) == 404:
                raise GmailHistoryStaleError("Gmail history is stale; a full sync is required.") from exc
            raise
        return _dedupe(result)[:max_results], latest_history_id

    def get_message(self, message_id: str) -> GmailMessageRecord:
        payload = self.service.users().messages().get(userId="me", id=message_id, format="full").execute()
        return _record_from_message(payload, self.account_id)


def _record_from_message(message: dict[str, Any], account_id: str) -> GmailMessageRecord:
    headers = _headers(message)
    label_ids = [str(item) for item in message.get("labelIds", [])]
    snippet = str(message.get("snippet") or "")
    body_preview = _body_preview(message) or snippet
    return GmailMessageRecord(
        provider="gmail",
        account_id=account_id,
        message_id=str(message.get("id") or ""),
        thread_id=str(message.get("threadId") or ""),
        history_id=str(message.get("historyId") or ""),
        direction="outbound" if "SENT" in label_ids else "inbound",
        sent_at=_internal_date(message.get("internalDate")) or headers.get("date", ""),
        from_text=headers.get("from", ""),
        to_text=headers.get("to", ""),
        subject=headers.get("subject", ""),
        snippet=snippet,
        body_preview=body_preview,
        label_ids=label_ids,
    )


def _headers(message: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in message.get("payload", {}).get("headers", []):
        name = str(item.get("name") or "").lower()
        if name:
            result[name] = str(item.get("value") or "")
    return result


def _internal_date(value: Any) -> str:
    try:
        timestamp = int(str(value)) / 1000
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _body_preview(message: dict[str, Any], limit: int = 12000) -> str:
    payload = message.get("payload", {})
    parts = _payload_text_parts(payload)
    text = "\n\n".join(part for part in parts if part.strip())
    return _compact_body_text(text)[:limit]


def _payload_text_parts(payload: dict[str, Any]) -> list[str]:
    mime_type = str(payload.get("mimeType") or "")
    body_text = _decode_body(payload.get("body", {}))
    if body_text and mime_type in {"text/plain", "text/html", ""}:
        return [_html_to_text(body_text) if mime_type == "text/html" else body_text]
    result: list[str] = []
    for part in payload.get("parts", []) or []:
        if isinstance(part, dict):
            result.extend(_payload_text_parts(part))
    return result


def _decode_body(body: dict[str, Any]) -> str:
    data = str((body or {}).get("data") or "")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return _compact_body_text(text)


def _compact_body_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _client_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "web": {
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [config["redirect_uri"]],
        }
    }


def _build_service(credentials: Any):
    build = _google_build()
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _google_flow():
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as exc:
        raise ValueError("Gmail support needs google-auth-oauthlib. Install app/environment/requirements.txt.") from exc
    return Flow


def _google_credentials():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise ValueError("Gmail support needs google-auth and google-auth-oauthlib.") from exc
    return Credentials, Request


def _google_build():
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ValueError("Gmail support needs google-api-python-client.") from exc
    return build


def _google_http_error():
    try:
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise ValueError("Gmail support needs google-api-python-client.") from exc
    return HttpError


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result
