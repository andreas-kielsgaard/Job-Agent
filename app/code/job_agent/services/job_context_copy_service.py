from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_agent.config import ROOT, load_profile
from job_agent.generator import select_experience, select_skills
from job_agent.models import Job, MatchResult
from job_agent.prompt_context import APP_CONTEXT
from job_agent.services.application_examples_service import ApplicationExamplesService, format_examples_for_prompt


class JobContextCopyService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def build(self, package: dict[str, Any], files: dict[str, str], status: Any) -> str:
        job = _job_from_files(files)
        match = _match_from_files(files, package)
        profile = load_profile(self.root)
        relevant_profile = self._relevant_profile(profile, job, match)
        material_sections = _material_sections(files)

        parts = [
            "# Job Agent Copy Context",
            "",
            (
                "Use this as background for whatever question I ask next. I am reviewing a job in a "
                "local preparation app, not asking you to submit an application or contact anyone. "
                "Use the evidence below, keep claims factual, and call out uncertainty."
            ),
            "",
            "## App Context",
            APP_CONTEXT,
            "",
            "## Job Summary",
            _job_summary(package, status),
            "",
            "## Job Package Index",
            _json_block({key: value for key, value in package.items() if key != "_index_path"}),
            "",
            "## Job JSON",
            files.get("job", "[No job JSON saved]"),
            "",
            "## Match JSON",
            files.get("match", "[No match JSON saved]"),
            "",
            "## Relevant Profile Data",
            _json_block(relevant_profile),
            "",
            "## Canonical CV",
            str(profile.get("canonical_cv") or "[No canonical CV text configured]").strip(),
            "",
            "## Writing Style",
            str(profile.get("writing_style") or "[No writing style configured]").strip(),
            "",
            "## Relevant Human-Edited Application Examples",
            self._application_examples(job, match, profile),
            "",
            "## Generated Materials",
            material_sections,
        ]
        return "\n".join(parts).strip() + "\n"

    def _relevant_profile(self, profile: dict[str, Any], job: Job | None, match: MatchResult) -> dict[str, Any]:
        selected_experience = select_experience(job, profile) if job else []
        top_skills = select_skills(job, match, profile) if job else []
        return {
            "contact": profile.get("contact", {}),
            "availability": profile.get("availability", {}),
            "location_policy": profile.get("location_policy", {}),
            "role_preferences": profile.get("role_preferences", {}),
            "target_roles": profile.get("target_roles", {}),
            "top_skills_for_this_job": top_skills,
            "selected_experience_for_this_job": selected_experience,
            "skills": profile.get("skills", {}),
            "experience": profile.get("experience", []),
            "match_engine": profile.get("match_engine", {}),
        }

    def _application_examples(self, job: Job | None, match: MatchResult, profile: dict[str, Any]) -> str:
        if not job:
            return "[No job parsed; relevant examples could not be selected]"
        examples = ApplicationExamplesService(self.root).select_relevant(job, match, profile)
        return format_examples_for_prompt(examples) or "[No relevant examples configured]"


def _job_from_files(files: dict[str, str]) -> Job | None:
    try:
        return Job.from_mapping(json.loads(files.get("job", "{}")))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _match_from_files(files: dict[str, str], package: dict[str, Any]) -> MatchResult:
    try:
        return MatchResult(**json.loads(files.get("match", "{}")))
    except (TypeError, ValueError, json.JSONDecodeError):
        return MatchResult(
            total_score=int(package.get("match_score") or 0),
            category=str(package.get("match_category") or "not_scored"),
            recommended_angle=str(package.get("recommended_angle") or ""),
            concerns=[str(item) for item in package.get("concerns", [])],
        )


def _job_summary(package: dict[str, Any], status: Any) -> str:
    status_text = getattr(status, "status", "") if status else ""
    notes = getattr(status, "notes", "") if status else ""
    not_interesting_reason = getattr(status, "not_interesting_reason", "") if status else ""
    lines = [
        f"Title: {package.get('title', '')}",
        f"Company/recruiter: {package.get('company', '')} / {package.get('recruiter', '')}",
        f"Source: {package.get('source', '')} ({package.get('source_id', '')})",
        f"Location: {package.get('location', '')}",
        f"Remote/onsite: {package.get('remote', '')}",
        f"Rate: {package.get('rate', '')}",
        f"Workload: {package.get('workload', '')}",
        f"Source URL: {package.get('source_url', '')}",
        f"Application URL: {package.get('application_url', '')}",
        f"Match: {package.get('match_score', '')}% / {package.get('match_category', '')}",
        f"Recommended angle: {package.get('recommended_angle', '')}",
        "Concerns: " + "; ".join(str(item) for item in package.get("concerns", [])),
        f"Application status: {status_text or package.get('application_status', 'unreviewed')}",
    ]
    if notes:
        lines.append(f"Private notes: {notes}")
    if not_interesting_reason:
        lines.append(f"Not interesting reason: {not_interesting_reason}")
    return "\n".join(lines)


def _material_sections(files: dict[str, str]) -> str:
    labels = {
        "cv": "At-a-glance CV",
        "application": "Application Text",
        "form_answers": "Form Answers",
        "match_analysis": "Match Analysis",
    }
    sections = []
    for key, label in labels.items():
        content = files.get(key, "").strip() or "[Not generated yet]"
        sections.append(f"### {label}\n{content}")
    return "\n\n".join(sections)


def _json_block(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)
