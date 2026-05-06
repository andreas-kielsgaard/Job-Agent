from __future__ import annotations

from typing import Any


class ReviewBundleService:
    def build(self, package: dict, files: dict[str, str], status: Any) -> str:
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
            "## Form Answers",
            files.get("form_answers", "[Not generated yet]"),
        ]
        return "\n".join(parts)
