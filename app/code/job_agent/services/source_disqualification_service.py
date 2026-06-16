from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_agent.config import ROOT
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.paths import sources_dir


@dataclass(frozen=True)
class SourceDomainDisqualification:
    domain: str
    reason: str
    source: str = "manual"
    created_at: str = ""


DEFAULT_DOMAIN_DISQUALIFICATIONS: tuple[SourceDomainDisqualification, ...] = (
    SourceDomainDisqualification(
        domain="jobs.github.com",
        reason="GitHub Jobs was discontinued and should not be suggested as a job source.",
        source="default",
    ),
    SourceDomainDisqualification(
        domain="stackoverflow.com",
        reason="Stack Overflow Jobs/Careers was discontinued and should not be suggested as a job source.",
        source="default",
    ),
    SourceDomainDisqualification(
        domain="careers.stackoverflow.com",
        reason="Stack Overflow Jobs/Careers was discontinued and should not be suggested as a job source.",
        source="default",
    ),
    SourceDomainDisqualification(
        domain="indeed.com",
        reason="Indeed suggestions were manually disqualified because the supplied links did not show relevant current jobs.",
        source="default",
    ),
    SourceDomainDisqualification(
        domain="linkedin.com",
        reason="LinkedIn job pages are login-heavy/protected and previous suggested LinkedIn sources overlapped or failed setup.",
        source="default",
    ),
)

DISQUALIFIED_NAME_TERMS: tuple[tuple[str, str], ...] = (
    ("github jobs", "GitHub Jobs was discontinued and should not be suggested as a job source."),
    ("stack overflow jobs", "Stack Overflow Jobs/Careers was discontinued and should not be suggested."),
    ("stackoverflow jobs", "Stack Overflow Jobs/Careers was discontinued and should not be suggested."),
    (
        "indeed",
        "Indeed suggestions were manually disqualified because supplied links did not show relevant current jobs.",
    ),
    ("linkedin", "LinkedIn suggestions are skipped because prior LinkedIn sources overlapped or failed setup."),
)


class SourceDisqualificationService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.path = sources_dir(self.root) / "source-disqualifications.yaml"

    def list_domains(self) -> list[SourceDomainDisqualification]:
        records = list(DEFAULT_DOMAIN_DISQUALIFICATIONS)
        data = read_yaml(self.path, {"domains": []})
        raw_domains = data.get("domains", []) if isinstance(data, dict) else []
        if isinstance(raw_domains, list):
            records.extend(_record_from_mapping(item) for item in raw_domains if isinstance(item, dict))
        deduped: dict[str, SourceDomainDisqualification] = {}
        for record in records:
            domain = normalize_domain(record.domain)
            if not domain or domain in deduped:
                continue
            deduped[domain] = SourceDomainDisqualification(
                domain=domain,
                reason=record.reason,
                source=record.source,
                created_at=record.created_at,
            )
        return sorted(deduped.values(), key=lambda item: (item.source != "default", item.domain))

    def add_domain(
        self, domain_or_url: str, *, reason: str = "", source: str = "manual"
    ) -> SourceDomainDisqualification:
        domain = normalize_domain(domain_or_url)
        if not domain:
            raise ValueError("Enter a domain or public source URL to disqualify.")
        record = SourceDomainDisqualification(
            domain=domain,
            reason=reason.strip() or "Manually disqualified from source suggestions.",
            source=source.strip() or "manual",
            created_at=_now(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = read_yaml(self.path, {"domains": []})
        if not isinstance(data, dict):
            data = {"domains": []}
        domains = data.setdefault("domains", [])
        if not isinstance(domains, list):
            domains = []
            data["domains"] = domains
        domains = [
            item
            for item in domains
            if not isinstance(item, dict) or normalize_domain(str(item.get("domain") or "")) != domain
        ]
        domains.append(
            {
                "domain": record.domain,
                "reason": record.reason,
                "source": record.source,
                "created_at": record.created_at,
            }
        )
        data["domains"] = domains
        write_yaml(self.path, data)
        return record

    def disqualification_for_url(self, url: str) -> SourceDomainDisqualification | None:
        domain = normalize_domain(url)
        if not domain:
            return None
        for record in self.list_domains():
            if domain == record.domain or domain.endswith(f".{record.domain}"):
                return record
        return None

    def disqualification_for_suggestion(
        self,
        *,
        name: str,
        url: str,
    ) -> SourceDomainDisqualification | None:
        lowered_name = " ".join(name.lower().replace("-", " ").split())
        for term, reason in DISQUALIFIED_NAME_TERMS:
            if term in lowered_name:
                return SourceDomainDisqualification(
                    domain=normalize_domain(url) or term.replace(" ", "-"),
                    reason=reason,
                    source="default",
                )
        return self.disqualification_for_url(url)


def normalize_domain(domain_or_url: str) -> str:
    value = str(domain_or_url or "").strip().lower()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.hostname or ""
    return host.removeprefix("www.").strip(".")


def _record_from_mapping(data: dict[str, Any]) -> SourceDomainDisqualification:
    return SourceDomainDisqualification(
        domain=str(data.get("domain") or "").strip(),
        reason=str(data.get("reason") or "").strip(),
        source=str(data.get("source") or "manual").strip() or "manual",
        created_at=str(data.get("created_at") or "").strip(),
    )


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
