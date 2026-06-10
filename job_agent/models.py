from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text.lower()).strip()
    return text


def normalize_title(title: str) -> str:
    title = normalize_text(title)
    title = re.sub(r"\b(senior|sr|junior|jr|consultant|contractor|freelance)\b", "", title)
    title = re.sub(r"[^a-z0-9+#/ ]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def list_from_value(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


@dataclass
class Job:
    title: str
    company: str = "Unknown"
    recruiter: str = ""
    end_client: str = ""
    source: str = "Unknown"
    source_id: str = ""
    url: str = ""
    application_url: str = ""
    location: str = "Not listed"
    remote: str = "Not listed"
    rate: str = "Not listed"
    contract_duration: str = "Not listed"
    start_date: str = "Not listed"
    posted_date: str = "Not listed"
    deadline: str = "Not listed"
    workload: str = "Not listed"
    languages: list[str] = field(default_factory=list)
    description: str = ""
    first_seen_date: str = ""
    freshness_confidence: str = "unknown"
    normalized_title: str = ""
    role_category: str = "unknown"
    required_languages: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    required_modules: list[str] = field(default_factory=list)
    seniority: str = "unknown"
    source_confidence: str = "unknown"
    raw_text: str = ""
    extraction_notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.languages = list_from_value(self.languages)
        self.required_languages = list_from_value(self.required_languages or self.languages)
        self.required_skills = list_from_value(self.required_skills)
        self.required_modules = list_from_value(self.required_modules)
        self.extraction_notes = list_from_value(self.extraction_notes)
        if not self.normalized_title:
            self.normalized_title = normalize_title(self.title)
        if not self.application_url:
            self.application_url = self.url
        if not self.raw_text:
            self.raw_text = "\n".join(
                str(part)
                for part in [
                    self.title,
                    self.company,
                    self.recruiter,
                    self.end_client,
                    self.location,
                    self.remote,
                    self.rate,
                    self.contract_duration,
                    self.start_date,
                    self.posted_date,
                    self.deadline,
                    self.workload,
                    " ".join(self.languages),
                    self.description,
                ]
                if part
            )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Job:
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{name: data[name] for name in allowed if name in data})


@dataclass
class SourceWarning:
    source: str
    message: str
    url: str = ""


@dataclass
class SourceRunResult:
    jobs: list[Job] = field(default_factory=list)
    warnings: list[SourceWarning] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SeenJobRecord:
    stable_id: str
    fuzzy_key: str
    title: str
    company: str
    source: str
    url: str
    first_seen_date: str
    last_seen_date: str
    content_hash: str
    status: str = "new"
    listing_key: str = ""
    posting_status: str = "active"
    posting_status_updated_at: str = ""


@dataclass
class JobState:
    job: Job
    stable_id: str
    fuzzy_key: str
    content_hash: str
    status: str


@dataclass
class MatchResult:
    total_score: int
    category: str
    components: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    recommended_angle: str = ""
    exclusion_reason: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    review_triggers: list[str] = field(default_factory=list)
    review_trigger_labels: list[str] = field(default_factory=list)
    deterministic_confidence: str = "medium"

    @property
    def score(self) -> int:
        return self.total_score


@dataclass
class GeneratedPackage:
    cv: str
    application: str
    form_answers: str
    match_analysis: str
    selected_experience: list[dict[str, str]]
    top_skills: list[str]
    generation_notes: list[str] = field(default_factory=list)
