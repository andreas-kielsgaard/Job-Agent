from __future__ import annotations

import json
from pathlib import Path

from job_agent.email_models import GmailMessageRecord
from job_agent.email_store import GmailMessageStore, GmailThreadStore
from job_agent.io.json_store import read_json
from job_agent.llm import LlmCompletion
from job_agent.services.gmail_thread_posting_service import GmailThreadPostingService


def test_gmail_thread_builds_deterministic_manual_posting_input(project_root: Path) -> None:
    _seed_thread(project_root)

    posting = GmailThreadPostingService(project_root).build_posting_input("thread-1")

    assert posting.title == "SAP ABAP Consultant for client"
    assert posting.source == "Recruiter Mail"
    assert posting.location == "Copenhagen"
    assert posting.remote == "Hybrid"
    assert posting.rate == "DKK 900/hour"
    assert "Imported from recruiter Gmail thread" in posting.description
    assert "ABAP RAP CDS" in posting.description


def test_gmail_thread_llm_summary_refines_manual_posting_input(monkeypatch, project_root: Path) -> None:
    _seed_thread(project_root)

    class FakeLlmService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def is_configured(self) -> bool:
            return True

        def complete(self, prompt: str, **kwargs):
            assert "Email chain:" in prompt
            return LlmCompletion(
                text=json.dumps(
                    {
                        "title": "SAP ABAP/RAP Consultant",
                        "company": "End Client A",
                        "workload": "80%",
                        "description": "LLM summarized ABAP RAP role from recruiter chain.",
                    }
                ),
                model="fake-model",
            )

    monkeypatch.setattr("job_agent.services.gmail_thread_posting_service.LlmService", FakeLlmService)

    posting = GmailThreadPostingService(project_root).build_posting_input("thread-1", use_llm=True)

    assert posting.title == "SAP ABAP/RAP Consultant"
    assert posting.company == "End Client A"
    assert posting.location == "Copenhagen"
    assert posting.workload == "80%"
    assert posting.description == "LLM summarized ABAP RAP role from recruiter chain."


def test_gmail_thread_import_creates_manual_posting_package(template_project: Path) -> None:
    _seed_thread(template_project)

    result = GmailThreadPostingService(template_project).import_thread("thread-1")

    index = read_json(Path(result.package_paths["index"]), {})
    assert result.material_status == "missing"
    assert index["title"] == "SAP ABAP Consultant for client"
    assert index["source"] == "Recruiter Mail"


def _seed_thread(root: Path) -> None:
    messages = [
        GmailMessageRecord(
            provider="gmail",
            account_id="me@example.com",
            message_id="m1",
            thread_id="thread-1",
            direction="inbound",
            sent_at="2026-06-18T09:30:00+00:00",
            from_text="recruiter@example.com",
            to_text="me@example.com",
            subject="Re: SAP ABAP Consultant for client",
            snippet="Hybrid ABAP role",
            body_preview=(
                "Client: End Client A\n"
                "Location: Copenhagen\n"
                "Rate: DKK 900/hour\n"
                "We need an SAP ABAP consultant with ABAP RAP CDS and OData."
            ),
        ),
        GmailMessageRecord(
            provider="gmail",
            account_id="me@example.com",
            message_id="m2",
            thread_id="thread-1",
            direction="outbound",
            sent_at="2026-06-18T10:00:00+00:00",
            from_text="me@example.com",
            to_text="recruiter@example.com",
            subject="Re: SAP ABAP Consultant for client",
            snippet="I can do hybrid.",
            body_preview="I can do hybrid work in Copenhagen.",
        ),
    ]
    GmailMessageStore(root).upsert_many(messages)
    GmailThreadStore(root).upsert_from_messages(messages)
