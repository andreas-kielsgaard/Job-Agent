from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    title: str
    company: str = "Unknown"
    source: str = "Unknown"
    url: str = ""
    location: str = "Not listed"
    remote: str = "Not listed"
    rate: str = "Not listed"
    contract_duration: str = "Not listed"
    start_date: str = "Not listed"
    posted_date: str = "Not listed"
    description: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Job":
        return cls(**{field.name: data.get(field.name, getattr(cls, field.name, "")) for field in cls.__dataclass_fields__.values()})


@dataclass
class MatchResult:
    score: int
    reasons: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class GeneratedPackage:
    cv: str
    application: str
    form_answers: str
    selected_experience: list[dict[str, str]]
    top_skills: list[str]
