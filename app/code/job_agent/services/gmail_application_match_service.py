from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from job_agent.application_models import ApplicationRecord
from job_agent.application_store import ApplicationStore, EmailThreadLinkStore
from job_agent.config import ROOT
from job_agent.email_models import GmailMessageRecord
from job_agent.email_store import GmailMessageStore
from job_agent.services.application_tracker_service import ApplicationTrackerService
from job_agent.services.package_index_service import PackageIndexService

AUTO_LINK_THRESHOLD = 85
AUTO_LINK_GAP = 20
UNKNOWN_VALUES = {"", "unknown", "not listed", "recruiter"}
CONFIRMATION_TERMS = (
    "successfully submitted",
    "successfully applied",
    "thank you for applying",
    "thanks for applying",
    "application confirmation",
    "your application for",
    "vacancy application",
    "recent application",
    "unterlagen erhalten",
)
FOLLOW_UP_TERMS = (
    "recent application",
    "complete your applicant profile",
    "short questionnaire",
    "take survey",
    "we'd love to learn more",
)
STOP_TOKENS = {
    "a",
    "an",
    "and",
    "at",
    "consultant",
    "developer",
    "for",
    "job",
    "need",
    "only",
    "position",
    "role",
    "sap",
    "senior",
    "sent",
    "sr",
    "the",
    "to",
    "urgent",
}
SOURCE_ALIASES = {
    "dice": ("dice", "applyonline@dice.com"),
    "energize": ("energize", "energizerec.com", "energizerecruitment.com"),
    "experis": ("experis", "experis.pl"),
    "freelancermap": ("freelancermap",),
    "joyit": ("joyit", "projects@joyit.de"),
    "linkedin": ("linkedin",),
    "red global": ("red global", "redglobal", "redcommerce", "red commerce"),
    "whitehall": ("whitehall", "broadbean", "whitehallresources.com"),
    "workable": ("workable", "candidates.workablemail.com"),
}


@dataclass
class GmailApplicationThreadMatch:
    thread_id: str
    application_id: str
    confidence: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class GmailApplicationMatchResult:
    reviewed_threads: int = 0
    linked: list[GmailApplicationThreadMatch] = field(default_factory=list)
    skipped_existing: int = 0
    skipped_ambiguous: int = 0

    @property
    def linked_count(self) -> int:
        return len(self.linked)


@dataclass
class _ApplicationCandidate:
    record: ApplicationRecord
    package: dict
    title: str
    source: str
    source_aliases: set[str]
    title_tokens: set[str]
    applied_at: datetime | None


class GmailApplicationMatchService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.applications = ApplicationStore(self.root)
        self.thread_links = EmailThreadLinkStore(self.root)
        self.messages = GmailMessageStore(self.root)
        self.packages = PackageIndexService(self.root)
        self.tracker = ApplicationTrackerService(self.root)

    def match_cached_threads(self, thread_ids: set[str] | None = None) -> GmailApplicationMatchResult:
        self.tracker.backfill_applied()
        applications = [self._candidate(record) for record in self.applications.list_all()]
        messages_by_thread = self._messages_by_thread(thread_ids)
        existing = self._existing_link_keys()
        result = GmailApplicationMatchResult(reviewed_threads=len(messages_by_thread))
        for thread_id, messages in messages_by_thread.items():
            latest = messages[-1]
            key = ("gmail", latest.account_id.strip(), thread_id)
            if key in existing:
                result.skipped_existing += 1
                continue
            match = self._best_match(thread_id, messages, applications)
            if match is None:
                result.skipped_ambiguous += 1
                continue
            self.thread_links.link_thread(
                match.application_id,
                thread_id,
                account_id=latest.account_id,
                provider="gmail",
                linked_by="auto",
            )
            result.linked.append(match)
        return result

    def _candidate(self, record: ApplicationRecord) -> _ApplicationCandidate:
        package = self.packages.find_package(record.stable_id) or {}
        title = record.title or str(package.get("title") or "")
        source = record.source or str(package.get("source") or "")
        aliases = _source_aliases(source)
        for url_key in ["source_url", "application_url", "url"]:
            aliases.update(_url_aliases(str(getattr(record, url_key, "") or package.get(url_key) or "")))
        company = (record.company or str(package.get("company") or "")).strip()
        if _has_real_value(company):
            aliases.add(_normalize(company))
        return _ApplicationCandidate(
            record=record,
            package=package,
            title=title,
            source=source,
            source_aliases={alias for alias in aliases if alias},
            title_tokens=_significant_tokens(title),
            applied_at=_parse_date(record.applied_at),
        )

    def _messages_by_thread(self, thread_ids: set[str] | None) -> dict[str, list[GmailMessageRecord]]:
        result: dict[str, list[GmailMessageRecord]] = {}
        for message in self.messages.list_all():
            if not message.thread_id:
                continue
            if thread_ids is not None and message.thread_id not in thread_ids:
                continue
            result.setdefault(message.thread_id, []).append(message)
        for messages in result.values():
            messages.sort(key=lambda item: (item.sent_at, item.message_id))
        return result

    def _existing_link_keys(self) -> set[tuple[str, str, str]]:
        return {
            (link.provider, link.account_id, link.thread_id)
            for link in self.thread_links.list_all()
            if link.thread_id and link.status in {"linked", "rejected", "unlinked"}
        }

    def _best_match(
        self,
        thread_id: str,
        messages: list[GmailMessageRecord],
        applications: list[_ApplicationCandidate],
    ) -> GmailApplicationThreadMatch | None:
        scored = [
            candidate
            for candidate in [self._score_candidate(thread_id, messages, application) for application in applications]
            if candidate.confidence >= AUTO_LINK_THRESHOLD
        ]
        scored.sort(key=lambda item: item.confidence, reverse=True)
        if not scored:
            return None
        if len(scored) > 1 and scored[0].confidence - scored[1].confidence < AUTO_LINK_GAP:
            return None
        return scored[0]

    def _score_candidate(
        self,
        thread_id: str,
        messages: list[GmailMessageRecord],
        application: _ApplicationCandidate,
    ) -> GmailApplicationThreadMatch:
        text = _thread_text(messages)
        normalized_text = _normalize(text)
        confidence = 0
        reasons: list[str] = []

        source_evidence = _has_source_evidence(normalized_text, application.source_aliases)
        company_evidence = _has_company_evidence(normalized_text, application.record.company)
        url_evidence = _has_url_evidence(normalized_text, application)
        if source_evidence:
            confidence += 30
            reasons.append("source evidence")
        if company_evidence:
            confidence += 25
            reasons.append("company evidence")
        if url_evidence:
            confidence += 80
            reasons.append("job URL evidence")

        title_confidence = _title_confidence(normalized_text, application.title, application.title_tokens)
        if title_confidence:
            confidence += title_confidence
            reasons.append("title evidence")

        if _has_any_term(normalized_text, CONFIRMATION_TERMS):
            confidence += 25
            reasons.append("application confirmation language")

        proximity = _date_proximity_score(messages[-1].sent_at, application.applied_at)
        if proximity:
            confidence += proximity
            reasons.append("date proximity")

        if _source_only_follow_up(normalized_text, source_evidence, title_confidence, proximity):
            confidence = max(confidence, 90)
            reasons.append("source follow-up near application date")

        if not (url_evidence or source_evidence or company_evidence):
            confidence = min(confidence, 70)
        if _looks_like_different_company(normalized_text, application):
            confidence = min(confidence, 70)
            reasons.append("possible different company")

        return GmailApplicationThreadMatch(
            thread_id=thread_id,
            application_id=application.record.application_id,
            confidence=min(confidence, 100),
            reasons=reasons,
        )


def _thread_text(messages: list[GmailMessageRecord]) -> str:
    blocks = []
    for message in messages:
        blocks.append(
            "\n".join(
                [
                    message.from_text,
                    message.to_text,
                    message.subject,
                    message.snippet,
                    message.body_preview,
                ]
            )
        )
    return "\n\n".join(blocks)


def _source_aliases(source: str) -> set[str]:
    normalized = _normalize(source)
    aliases = {normalized} if normalized else set()
    for key, values in SOURCE_ALIASES.items():
        if key in normalized:
            aliases.update(_normalize(value) for value in values)
    return aliases


def _url_aliases(url: str) -> set[str]:
    if not url:
        return set()
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    aliases = {_normalize(host)} if host else set()
    return aliases


def _has_source_evidence(text: str, aliases: set[str]) -> bool:
    return any(alias and alias in text for alias in aliases)


def _has_company_evidence(text: str, company: str) -> bool:
    return _has_real_value(company) and _normalize(company) in text


def _has_url_evidence(text: str, application: _ApplicationCandidate) -> bool:
    for value in [
        application.record.source_url,
        application.record.application_url,
        str(application.package.get("source_url") or ""),
        str(application.package.get("application_url") or ""),
        str(application.package.get("url") or ""),
    ]:
        normalized = _normalize(value)
        if normalized and len(normalized) > 18 and normalized in text:
            return True
    return False


def _title_confidence(text: str, title: str, title_tokens: set[str]) -> int:
    normalized_title = _normalize(title)
    if normalized_title and len(normalized_title) >= 12 and normalized_title in text:
        return 50
    if not title_tokens:
        return 0
    overlap = len(title_tokens & set(text.split()))
    if overlap < max(1, min(2, len(title_tokens))):
        return 0
    return min(35, overlap * 12)


def _source_only_follow_up(text: str, source_evidence: bool, title_confidence: int, proximity: int) -> bool:
    return source_evidence and not title_confidence and proximity >= 15 and _has_any_term(text, FOLLOW_UP_TERMS)


def _date_proximity_score(message_date: str, applied_at: datetime | None) -> int:
    if applied_at is None:
        return 0
    message_at = _parse_date(message_date)
    if message_at is None:
        return 0
    days = abs((message_at - applied_at).total_seconds()) / 86400
    if days <= 1:
        return 15
    if days <= 7:
        return 8
    return 0


def _looks_like_different_company(text: str, application: _ApplicationCandidate) -> bool:
    company = _normalize(application.record.company)
    if company and company not in UNKNOWN_VALUES and company not in text:
        return False
    source_url = _normalize(application.record.source_url or application.record.application_url)
    slug_match = re.search(r"\bat ([a-z0-9]+(?: [a-z0-9]+){0,2})\b", source_url)
    if not slug_match:
        return False
    slug = slug_match.group(1).strip()
    if not slug or slug in UNKNOWN_VALUES:
        return False
    email_company = _company_from_email_text(text)
    return bool(email_company and email_company != slug and slug not in text)


def _company_from_email_text(text: str) -> str:
    for pattern in [
        r"\bthanks for applying to ([a-z0-9]+(?: [a-z0-9]+){0,2})\b",
        r"\byour application for .+? at ([a-z0-9]+(?: [a-z0-9]+){0,3}) (?:sent|was)\b",
        r"\bapplication for .+? at ([a-z0-9]+(?: [a-z0-9]+){0,3}) sent\b",
    ]:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _has_any_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(_normalize(term) in text for term in terms)


def _significant_tokens(value: str) -> set[str]:
    return {token for token in _normalize(value).split() if len(token) >= 2 and token not in STOP_TOKENS}


def _has_real_value(value: str) -> bool:
    return _normalize(value) not in UNKNOWN_VALUES


def _normalize(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"https?://", " ", text)
    text = re.sub(r"[^a-z0-9+#/@.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
