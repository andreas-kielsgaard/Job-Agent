from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import ROOT
from .io.json_store import read_json, write_json
from .services.llm_service import LlmService

APP_CONTEXT = """This is a local-first SAP freelance job preparation application.
It discovers SAP freelance/contract roles, scores them against a private profile,
and prepares recruiter-facing materials for human review. It must not submit
applications, create accounts, log in, upload files, or send emails automatically.
Keep generated text accurate, practical, and suitable for manual review."""


FIELD_CONTEXTS = {
    "profile.skills": "We are editing the structured skills and caveats used for scoring and generated applications. Preserve YAML structure and honest caveats.",
    "profile.experience": "We are editing structured work experience. Experience keywords are used to select the most relevant projects for tailored CVs.",
    "profile.canonical_cv": "We are editing the canonical CV text, the source of truth for generation. Keep it factual and machine-readable.",
    "profile.writing_style": "We are editing the general writing style used when generating application texts. Prefer clear rules over vague preference statements.",
    "sources": "We are editing job source configuration. Keep YAML valid and avoid enabling unreliable sources without noting limitations.",
    "template.cv": "We are editing a Jinja Markdown template for the at-a-glance CV. Preserve template variables and recruiter-facing tone.",
    "template.application": "We are editing a Jinja Markdown template for deterministic application text. Preserve template variables.",
    "template.form": "We are editing the standard form-answer package template. Do not imply real form inspection unless implemented.",
    "prompt.application": "We are editing the Claude prompt template for application generation. It uses Python .format placeholders.",
    "job.cv": "We are editing a generated at-a-glance CV for a specific role. Keep it recruiter-facing and accurate.",
    "job.application": "We are editing generated application text for a specific role. Keep the tone direct and do not exaggerate.",
    "job.form_answers": "We are editing generated standard form answers. Mark uncertain legal/rate/language items for manual confirmation.",
    "job.match_analysis": "We are editing internal match analysis. It can include score reasoning, concerns, and missing information.",
}


@dataclass
class ContextBlock:
    key: str
    label: str
    content: str


@dataclass
class EditContextPreference:
    button_id: str
    selected_blocks: list[str] = field(default_factory=list)
    disabled_blocks: list[str] = field(default_factory=list)


class PromptContextProvider:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def available_blocks(
        self, job_package: dict[str, Any] | None = None, job_files: dict[str, str] | None = None
    ) -> dict[str, ContextBlock]:
        blocks = {
            "app_context": ContextBlock("app_context", "App context", APP_CONTEXT),
            "personal_info": ContextBlock("personal_info", "Personal/contact info", self._read("profile/contact.yaml")),
            "preferences": ContextBlock(
                "preferences", "Availability/preferences", self._read("profile/preferences.yaml")
            ),
            "canonical_cv": ContextBlock("canonical_cv", "Canonical CV", self._read("profile/canonical-cv.md")),
            "skills": ContextBlock("skills", "Skills YAML", self._read("profile/skills.yaml")),
            "experience": ContextBlock("experience", "Experience YAML", self._read("profile/experience.yaml")),
            "writing_style": ContextBlock("writing_style", "Writing style", self._read("profile/writing-style.md")),
            "sources": ContextBlock("sources", "Sources YAML", self._read("sources/recruiting-sites.yaml")),
        }
        if job_package:
            blocks["job_package"] = ContextBlock(
                "job_package",
                "Job package index",
                json.dumps({k: v for k, v in job_package.items() if k != "_index_path"}, indent=2, ensure_ascii=False),
            )
        if job_files:
            if job_files.get("job"):
                blocks["job_json"] = ContextBlock("job_json", "Job JSON", job_files["job"])
            if job_files.get("match"):
                blocks["match_json"] = ContextBlock("match_json", "Match JSON", job_files["match"])
            if job_files.get("match_analysis"):
                blocks["match_analysis"] = ContextBlock("match_analysis", "Match analysis", job_files["match_analysis"])
        return blocks

    def default_blocks_for_field(self, field_id: str) -> list[str]:
        if field_id.startswith("job."):
            return [
                "app_context",
                "personal_info",
                "canonical_cv",
                "skills",
                "experience",
                "writing_style",
                "job_package",
                "job_json",
                "match_json",
            ]
        if field_id in {"profile.skills", "profile.experience"}:
            return ["app_context", "personal_info", "canonical_cv", "skills", "experience"]
        if field_id == "profile.writing_style":
            return ["app_context", "canonical_cv", "writing_style"]
        if field_id == "profile.canonical_cv":
            return ["app_context", "personal_info", "canonical_cv", "experience", "skills"]
        if field_id.startswith("template.") or field_id.startswith("prompt."):
            return ["app_context", "personal_info", "canonical_cv", "skills", "experience", "writing_style"]
        return ["app_context", "personal_info", "canonical_cv"]

    def build_prompt(
        self,
        *,
        field_id: str,
        current_text: str,
        user_instruction: str,
        selected_blocks: list[str],
        disabled_blocks: list[str],
        job_package: dict[str, Any] | None = None,
        job_files: dict[str, str] | None = None,
    ) -> str:
        blocks = self.available_blocks(job_package, job_files)
        included = [key for key in selected_blocks if key in blocks and key not in disabled_blocks]
        context_text = "\n\n".join(
            f"## {blocks[key].label}\n{blocks[key].content}" for key in included if blocks[key].content.strip()
        )
        field_context = FIELD_CONTEXTS.get(field_id, "We are editing a text field in the Job Agent application.")
        return f"""You are helping edit content inside Job Agent.

{field_context}

Return only the revised replacement text for the field. Do not wrap it in Markdown fences unless the field itself requires fenced code.

User instruction:
{user_instruction}

Current field text:
{current_text}

Relevant context:
{context_text}
"""

    def _read(self, relative_path: str) -> str:
        path = self.root / relative_path
        return path.read_text(encoding="utf-8") if path.exists() else ""


class EditContextPreferenceStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = root / "profile" / "ai_edit_contexts.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            write_json(self.path, {})

    def get(self, button_id: str, defaults: list[str]) -> EditContextPreference:
        data = read_json(self.path, {})
        item = data.get(button_id)
        if not item:
            return EditContextPreference(button_id=button_id, selected_blocks=defaults)
        return EditContextPreference(**item)

    def save(self, preference: EditContextPreference) -> None:
        data = read_json(self.path, {})
        data[preference.button_id] = asdict(preference)
        write_json(self.path, data)


def run_ai_edit(prompt: str, root: Path = ROOT) -> tuple[str, str]:
    completion = LlmService(root).complete(prompt, max_tokens=2200, purpose="ai_edit")
    return completion.text, completion.model
