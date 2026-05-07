from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from job_agent.models import Job

GENERIC_TITLE_LABELS = {
    "apply",
    "apply now",
    "apply today",
    "details",
    "learn more",
    "more",
    "more info",
    "read more",
    "see details",
    "services",
    "view",
    "view details",
    "view job",
    "view role",
}

MIN_USEFUL_DESCRIPTION_CHARS = 80


@dataclass
class CandidateQuality:
    title: str
    url: str
    title_quality: str
    description_length: int
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class ExtractionQuality:
    label: str
    status_code: int | None = None
    final_url: str = ""
    visible_text_chars: int = 0
    candidates: list[CandidateQuality] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def useful_title_count(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.title_quality == "useful")

    @property
    def generic_title_count(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.title_quality == "generic")

    @property
    def unique_url_count(self) -> int:
        return len({candidate.url for candidate in self.candidates if candidate.url})

    @property
    def average_description_length(self) -> int:
        if not self.candidates:
            return 0
        return round(mean(candidate.description_length for candidate in self.candidates))


def candidate_quality(job: Job) -> CandidateQuality:
    missing_fields = []
    if not job.title.strip():
        missing_fields.append("title")
    if not job.url.strip():
        missing_fields.append("url")
    if job.company == "Unknown":
        missing_fields.append("company")
    if job.location == "Not listed":
        missing_fields.append("location")
    if job.posted_date == "Not listed":
        missing_fields.append("posted_date")
    description_length = len(job.description.strip())
    if description_length < MIN_USEFUL_DESCRIPTION_CHARS:
        missing_fields.append("description")
    return CandidateQuality(
        title=job.title,
        url=job.url,
        title_quality=title_quality(job.title),
        description_length=description_length,
        missing_fields=missing_fields,
    )


def quality_as_dict(quality: ExtractionQuality) -> dict[str, Any]:
    return {
        "label": quality.label,
        "status_code": quality.status_code,
        "final_url": quality.final_url,
        "visible_text_chars": quality.visible_text_chars,
        "candidate_count": quality.candidate_count,
        "useful_title_count": quality.useful_title_count,
        "generic_title_count": quality.generic_title_count,
        "unique_url_count": quality.unique_url_count,
        "average_description_length": quality.average_description_length,
        "warnings": quality.warnings,
        "candidates": [candidate.__dict__ for candidate in quality.candidates],
    }


def title_quality(title: str) -> str:
    normalized = " ".join(title.lower().split())
    if normalized in GENERIC_TITLE_LABELS:
        return "generic"
    if len(normalized) < 8:
        return "generic"
    return "useful"
