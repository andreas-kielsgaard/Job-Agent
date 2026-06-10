from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_agent.config import ROOT, load_profile
from job_agent.models import Job, MatchResult
from job_agent.services.application_examples_service import ApplicationExamplesService, format_examples_for_prompt


class ReviewBundleService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def build(self, package: dict, files: dict[str, str], status: Any) -> str:
        examples = self._application_examples(files)
        parts = [
            "# External Agent Review Bundle",
            "",
            "Please review and suggest improvements to the application materials below. Keep claims accurate and do not invent experience.",
            "",
            "## Role",
            f"Title: {package.get('title', '')}",
            f"Company/recruiter: {package.get('company', '')} / {package.get('recruiter', '')}",
            f"Location: {package.get('location', '')}",
            f"Remote/onsite: {package.get('remote', '')}",
            f"Rate: {package.get('rate', '')}",
            f"Source URL: {package.get('source_url', '')}",
            f"Application URL: {package.get('application_url', '')}",
            "",
            "## Match",
            f"Score/category: {package.get('match_score', '')}% / {package.get('match_category', '')}",
            f"Recommended angle: {package.get('recommended_angle', '')}",
            "Concerns: " + "; ".join(package.get("concerns", [])),
            f"Application status: {getattr(status, 'status', 'unreviewed') if status else 'unreviewed'}",
            "",
            "## Job JSON",
            files.get("job", ""),
            "",
            "## Match Analysis",
            files.get("match_analysis", "[Not generated yet]"),
            "",
            "## At-a-glance CV",
            files.get("cv", "[Not generated yet]"),
            "",
            "## Application Text",
            files.get("application", "[Not generated yet]"),
            "",
            "## Human-edited Application Examples",
            examples or "[No relevant examples configured]",
            "",
            "## Form Answers",
            files.get("form_answers", "[Not generated yet]"),
        ]
        return "\n".join(parts)

    def _application_examples(self, files: dict[str, str]) -> str:
        try:
            job = Job.from_mapping(json.loads(files.get("job", "{}")))
            match = MatchResult(**json.loads(files.get("match", "{}")))
        except (TypeError, ValueError, json.JSONDecodeError):
            return ""
        profile = load_profile(self.root)
        examples = ApplicationExamplesService(self.root).select_relevant(job, match, profile)
        return format_examples_for_prompt(examples)
