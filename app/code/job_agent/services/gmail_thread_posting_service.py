from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.email_models import GmailMessageRecord, GmailThreadRecord
from job_agent.email_store import GmailMessageStore, GmailThreadStore
from job_agent.llm import LlmService
from job_agent.services.manual_posting_service import ManualPostingInput, ManualPostingResult, ManualPostingService


class GmailThreadPostingService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.messages = GmailMessageStore(self.root)
        self.threads = GmailThreadStore(self.root)

    def list_thread_options(self) -> list[dict[str, Any]]:
        messages_by_thread = self._messages_by_thread()
        records = self.threads.list_all()
        seen = {record.thread_id for record in records}
        for thread_id, messages in messages_by_thread.items():
            if thread_id not in seen and messages:
                latest = messages[-1]
                records.append(
                    GmailThreadRecord(
                        provider=latest.provider,
                        account_id=latest.account_id,
                        thread_id=thread_id,
                        history_id=latest.history_id,
                        subject=latest.subject,
                        snippet=latest.snippet,
                        message_ids=[message.message_id for message in messages],
                        last_message_at=latest.sent_at,
                    )
                )
        records.sort(key=lambda item: (item.last_message_at, item.thread_id), reverse=True)
        return [self._thread_option(record, messages_by_thread.get(record.thread_id, [])) for record in records]

    def build_posting_input(
        self,
        thread_id: str,
        *,
        use_llm: bool = False,
        llm_model: str = "",
    ) -> ManualPostingInput:
        thread, messages = self._thread_and_messages(thread_id)
        if not thread and not messages:
            raise KeyError(thread_id)
        data = _deterministic_posting(thread, messages)
        if use_llm:
            data = _merge_posting(data, self._llm_posting(thread, messages, llm_model=llm_model))
        return data

    def import_thread(
        self,
        thread_id: str,
        *,
        use_llm: bool = False,
        llm_model: str = "",
        ai_enhanced_search: bool = False,
        generate_materials: bool = False,
    ) -> ManualPostingResult:
        posting = self.build_posting_input(thread_id, use_llm=use_llm, llm_model=llm_model)
        posting = replace(
            posting,
            ai_enhanced_search=ai_enhanced_search,
            generate_materials=generate_materials,
            use_llm=use_llm,
            llm_model=llm_model,
        )
        return ManualPostingService(self.root).import_posting(posting)

    def _thread_option(self, thread: GmailThreadRecord, messages: list[GmailMessageRecord]) -> dict[str, Any]:
        latest = messages[-1] if messages else None
        subject = thread.subject or (latest.subject if latest else "") or thread.thread_id
        return {
            "thread_id": thread.thread_id,
            "account_id": thread.account_id or (latest.account_id if latest else ""),
            "subject": subject,
            "snippet": thread.snippet or (latest.snippet if latest else ""),
            "last_message_at": _display_date(thread.last_message_at or (latest.sent_at if latest else "")),
            "message_count": len(thread.message_ids) if thread.message_ids else len(messages),
            "participants": _participants(messages),
        }

    def _thread_and_messages(self, thread_id: str) -> tuple[GmailThreadRecord | None, list[GmailMessageRecord]]:
        thread_id = thread_id.strip()
        thread = self.threads.get_many({thread_id}).get(thread_id)
        messages = self.messages.list_for_thread_ids({thread_id})
        messages.sort(key=lambda item: (item.sent_at, item.message_id))
        return thread, messages

    def _messages_by_thread(self) -> dict[str, list[GmailMessageRecord]]:
        result: dict[str, list[GmailMessageRecord]] = {}
        for message in self.messages.list_all():
            if message.thread_id:
                result.setdefault(message.thread_id, []).append(message)
        for messages in result.values():
            messages.sort(key=lambda item: (item.sent_at, item.message_id))
        return result

    def _llm_posting(
        self,
        thread: GmailThreadRecord | None,
        messages: list[GmailMessageRecord],
        *,
        llm_model: str = "",
    ) -> ManualPostingInput:
        llm = LlmService(self.root)
        if not llm.is_configured():
            return ManualPostingInput()
        completion = llm.complete(
            _llm_prompt(thread, messages),
            max_tokens=1600,
            purpose="gmail_thread_job_intake",
            model=llm_model,
        )
        payload = _json_payload(completion.text)
        return _posting_from_payload(payload)


def _deterministic_posting(
    thread: GmailThreadRecord | None,
    messages: list[GmailMessageRecord],
) -> ManualPostingInput:
    subject = _clean_subject((thread.subject if thread else "") or (messages[-1].subject if messages else ""))
    chain_text = _chain_text(messages)
    full_text = f"{subject}\n{chain_text}".strip()
    url = _first_url(full_text)
    return ManualPostingInput(
        title=subject or _title_from_text(full_text),
        source="Recruiter Mail",
        company=_field_after_label(full_text, ["client", "company", "end client"]) or "Unknown",
        url=url,
        application_url=url,
        location=_field_after_label(full_text, ["location", "place"]) or "Not listed",
        remote=_remote_from_text(full_text),
        rate=_field_after_label(full_text, ["rate", "hourly rate", "daily rate", "budget"]) or "Not listed",
        workload=_field_after_label(full_text, ["workload", "capacity", "allocation"]) or "Not listed",
        posted_date=_display_date(messages[0].sent_at) if messages else "Not listed",
        description=_description(subject, messages),
    )


def _merge_posting(base: ManualPostingInput, update: ManualPostingInput) -> ManualPostingInput:
    values = {}
    for field in ManualPostingInput.__dataclass_fields__:
        current = getattr(base, field)
        candidate = getattr(update, field)
        values[field] = candidate if _has_value(candidate) else current
    return ManualPostingInput(**values)


def _posting_from_payload(payload: dict[str, Any]) -> ManualPostingInput:
    allowed = set(ManualPostingInput.__dataclass_fields__)
    values = {key: str(value).strip() for key, value in payload.items() if key in allowed and value is not None}
    return ManualPostingInput(**values)


def _llm_prompt(thread: GmailThreadRecord | None, messages: list[GmailMessageRecord]) -> str:
    return f"""
You summarize recruiter email chains into a single job posting for a local job tracking app.

Rules:
- Return only strict JSON.
- Do not invent facts. Use "Not listed" for missing logistics and "Unknown" for unknown company.
- Keep description factual and useful for matching: role, responsibilities, required skills, location/remote, rate, workload, duration, start, client/recruiter details, and open questions.
- Do not draft a reply and do not suggest sending email.

Return JSON with these string fields:
title, source, company, url, application_url, location, remote, rate, workload, posted_date, description

Thread subject: {(thread.subject if thread else "")}

Email chain:
{_chain_text(messages, limit=18000)}
""".strip()


def _chain_text(messages: list[GmailMessageRecord], limit: int = 14000) -> str:
    blocks = []
    for message in messages:
        body = message.body_preview or message.snippet
        blocks.append(
            "\n".join(
                [
                    f"Date: {message.sent_at or 'Not listed'}",
                    f"From: {message.from_text or 'Unknown'}",
                    f"To: {message.to_text or 'Unknown'}",
                    f"Subject: {message.subject or 'No subject'}",
                    f"Direction: {message.direction}",
                    "",
                    body,
                ]
            ).strip()
        )
    return "\n\n---\n\n".join(blocks)[:limit]


def _description(subject: str, messages: list[GmailMessageRecord]) -> str:
    parts = [
        "Imported from recruiter Gmail thread.",
        f"Thread subject: {subject or 'Not listed'}",
        "",
        "Email chain evidence:",
        _chain_text(messages),
    ]
    return "\n".join(parts).strip()


def _clean_subject(value: str) -> str:
    text = value.strip()
    text = re.sub(r"(?i)^(\s*(re|fw|fwd)\s*:\s*)+", "", text)
    text = re.sub(r"(?i)^\[external\]\s*", "", text).strip()
    return text[:140]


def _title_from_text(value: str) -> str:
    for line in value.splitlines():
        line = line.strip(" -:\t")
        if len(line) >= 8:
            return line[:120]
    return "Recruiter email opportunity"


def _field_after_label(value: str, labels: list[str]) -> str:
    for label in labels:
        match = re.search(rf"(?im)^\s*{re.escape(label)}\s*[:\-]\s*(.+)$", value)
        if match:
            return match.group(1).strip()[:160]
    return ""


def _remote_from_text(value: str) -> str:
    lowered = value.lower()
    if "fully remote" in lowered or "100% remote" in lowered:
        return "Fully remote"
    if "hybrid" in lowered:
        return "Hybrid"
    if "onsite" in lowered or "on-site" in lowered:
        return "Onsite"
    return _field_after_label(value, ["remote", "onsite", "work model"]) or "Not listed"


def _first_url(value: str) -> str:
    match = re.search(r"https?://[^\s<>)\"']+", value)
    return match.group(0).rstrip(".,;") if match else ""


def _participants(messages: list[GmailMessageRecord]) -> str:
    participants = []
    for message in messages:
        for value in [message.from_text, message.to_text]:
            text = value.strip()
            if text and text not in participants:
                participants.append(text)
    return ", ".join(participants[:4])


def _json_payload(value: str) -> dict[str, Any]:
    text = value.strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _has_value(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text not in {"Unknown", "Not listed"}


def _display_date(value: str) -> str:
    return value[:16].replace("T", " ") if "T" in value else value
