from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from job_agent.config import ROOT
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.models import Job, MatchResult
from job_agent.paths import profile_dir
from job_agent.run_store import utc_now


class ApplicationExamplesService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.path = profile_dir(root) / "application-examples.yaml"

    def list_examples(self) -> list[dict[str, Any]]:
        data = read_yaml(self.path, {})
        if isinstance(data, list):
            raw_examples = data
        elif isinstance(data, dict):
            raw_examples = data.get("application_examples", [])
        else:
            raw_examples = []
        return [_normalize_example(item) for item in raw_examples if isinstance(item, dict)]

    def save_examples(self, examples: list[dict[str, Any]]) -> None:
        write_yaml(self.path, {"application_examples": [_normalize_example(item) for item in examples]})

    def select_relevant(
        self,
        job: Job,
        match: MatchResult | None,
        profile: dict[str, Any] | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        profile = profile or {}
        haystack = _job_haystack(job, match)
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for index, example in enumerate(self.list_examples()):
            text = str(example.get("application_text") or "").strip()
            if not text:
                continue
            score = _example_score(example, haystack, job)
            if score <= 0 and not _has_profile_overlap(example, profile):
                continue
            scored.append((score, -index, example))
        return [item for _score, _index, item in sorted(scored, reverse=True)[:limit]]

    def upsert_from_form_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        existing = {str(item.get("id") or ""): item for item in self.list_examples()}
        saved: list[dict[str, Any]] = []
        for row in rows:
            label = str(row.get("label") or "").strip()
            application_text = str(row.get("application_text") or "").strip()
            if not label and not application_text:
                continue
            example_id = str(row.get("id") or "").strip()
            previous = existing.get(example_id, {})
            now = utc_now()
            saved.append(
                _normalize_example(
                    {
                        **previous,
                        **row,
                        "id": example_id or _new_id(label or "application-example"),
                        "label": label or "Application example",
                        "created_at": previous.get("created_at") or now,
                        "updated_at": now,
                    }
                )
            )
        self.save_examples(saved)
        return saved


def format_examples_for_prompt(examples: list[dict[str, Any]]) -> str:
    parts = []
    for example in examples:
        text = str(example.get("application_text") or "").strip()
        if not text:
            continue
        linked = example.get("linked_job") if isinstance(example.get("linked_job"), dict) else {}
        context = ", ".join(
            part
            for part in [
                str(linked.get("title") or "").strip(),
                str(linked.get("company") or "").strip(),
                _joined(example.get("linked_skills")),
                _joined(example.get("linked_modules")),
                _joined(example.get("linked_roles")),
            ]
            if part
        )
        heading = str(example.get("label") or "Application example").strip()
        parts.append(f"### {heading}\nContext: {context or 'No linked context'}\n{text}")
    return "\n\n".join(parts)


def _normalize_example(item: dict[str, Any]) -> dict[str, Any]:
    linked_job = item.get("linked_job") if isinstance(item.get("linked_job"), dict) else {}
    return {
        "id": str(item.get("id") or "").strip(),
        "label": str(item.get("label") or "").strip(),
        "application_text": str(item.get("application_text") or "").strip(),
        "linked_job": {
            "stable_id": str(linked_job.get("stable_id") or item.get("job_id") or "").strip(),
            "title": str(linked_job.get("title") or item.get("job_title") or "").strip(),
            "company": str(linked_job.get("company") or item.get("company") or "").strip(),
            "url": str(linked_job.get("url") or item.get("url") or "").strip(),
        },
        "linked_skills": _terms(item.get("linked_skills")),
        "linked_modules": _terms(item.get("linked_modules")),
        "linked_roles": _terms(item.get("linked_roles")),
        "notes": str(item.get("notes") or "").strip(),
        "created_at": str(item.get("created_at") or "").strip(),
        "updated_at": str(item.get("updated_at") or "").strip(),
    }


def _example_score(example: dict[str, Any], haystack: str, job: Job) -> int:
    score = 0
    for field in ["linked_skills", "linked_modules", "linked_roles"]:
        for term in _terms(example.get(field)):
            if _term_matches(haystack, term):
                score += 4
    linked_job = example.get("linked_job") if isinstance(example.get("linked_job"), dict) else {}
    title = str(linked_job.get("title") or "").strip()
    company = str(linked_job.get("company") or "").strip()
    if title and any(_term_matches(haystack, term) for term in _terms(title)):
        score += 2
    if company and company.lower() == (job.company or "").lower():
        score += 1
    return score


def _has_profile_overlap(example: dict[str, Any], profile: dict[str, Any]) -> bool:
    profile_terms = _terms(profile.get("skills", {}).get("strongest", []))
    target_roles = profile.get("target_roles", {})
    if isinstance(target_roles, dict):
        for values in target_roles.values():
            profile_terms.extend(_terms(values))
    linked = _terms(example.get("linked_skills")) + _terms(example.get("linked_roles"))
    return any(term in profile_terms for term in linked)


def _job_haystack(job: Job, match: MatchResult | None) -> str:
    parts = [
        job.title,
        job.company,
        job.description,
        job.raw_text,
        " ".join(job.required_skills),
        " ".join(job.required_modules),
        " ".join(match.matched_keywords if match else []),
    ]
    return " ".join(part for part in parts if part).lower()


def _new_id(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "application-example"
    return f"{slug}-{uuid4().hex[:8]}"


def _term_matches(text: str, term: str) -> bool:
    term = term.strip().lower()
    return bool(term and re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))


def _terms(value: Any) -> list[str]:
    source = value if isinstance(value, list) else re.split(r"[\n,]+", str(value or ""))
    result: list[str] = []
    seen: set[str] = set()
    for item in source:
        text = str(item).strip()
        key = text.lower()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _joined(value: Any) -> str:
    return ", ".join(_terms(value))
