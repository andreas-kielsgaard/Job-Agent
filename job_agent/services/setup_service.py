from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from job_agent.config import ROOT
from job_agent.io.atomic import atomic_write_text
from job_agent.io.yaml_store import read_yaml, write_yaml


def lines_to_list(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


class SetupService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def ensure_private_profile(self) -> None:
        profile = self.root / "profile"
        if not profile.exists() and (self.root / "profile.example").exists():
            shutil.copytree(self.root / "profile.example", profile)

    def load_profile_for_setup(self) -> dict[str, Any]:
        self.ensure_private_profile()
        return {
            "contact": self.load_yaml_file("profile/contact.yaml").get("contact", {}),
            "preferences": self.load_yaml_file("profile/preferences.yaml"),
        }

    def setup_files(self) -> dict[str, dict[str, str]]:
        return {
            "skills": {"label": "Skills and caveats", "field_id": "profile.skills", "path": "profile/skills.yaml", "content": self.read_text("profile/skills.yaml"), "help": "Skills are used by scoring and generation. Keep caveats honest; they are explicitly referenced in application text."},
            "experience": {"label": "Experience", "field_id": "profile.experience", "path": "profile/experience.yaml", "content": self.read_text("profile/experience.yaml"), "help": "Experience entries are scored by keywords. The two most relevant entries are selected for the at-a-glance CV."},
            "canonical_cv": {"label": "Canonical CV text", "field_id": "profile.canonical_cv", "path": "profile/canonical-cv.md", "content": self.read_text("profile/canonical-cv.md"), "help": "This is the main source-of-truth text given to Claude for writing."},
            "writing_style": {"label": "Writing style", "field_id": "profile.writing_style", "path": "profile/writing-style.md", "content": self.read_text("profile/writing-style.md"), "help": "Used in Claude prompts and as guidance for deterministic writing."},
            "sources": {"label": "Sources", "field_id": "sources", "path": "sources/recruiting-sites.yaml", "content": self.read_text("sources/recruiting-sites.yaml"), "help": "Enabled sources are read by the run service. local_yaml is safest; generic_html is best-effort."},
            "cv_template": {"label": "At-a-glance CV template", "field_id": "template.cv", "path": "templates/at-a-glance-cv.md.j2", "content": self.read_text("templates/at-a-glance-cv.md.j2"), "help": "Jinja template. Use {{ contact.name }}, {{ top_skills }}, {{ selected_experience }}, etc."},
            "application_template": {"label": "Application template", "field_id": "template.application", "path": "templates/application-letter.md.j2", "content": self.read_text("templates/application-letter.md.j2"), "help": "Deterministic fallback template used when Claude is disabled or fails."},
            "form_template": {"label": "Form answers template", "field_id": "template.form", "path": "templates/form-answers.md.j2", "content": self.read_text("templates/form-answers.md.j2"), "help": "Standard form answer package. Do not imply actual form inspection here."},
            "application_prompt": {"label": "Claude application prompt", "field_id": "prompt.application", "path": "prompts/generate_application.md", "content": self.read_text("prompts/generate_application.md"), "help": "Prompt template for Claude application generation. Variables use Python .format style: {canonical_cv}, {title}, {description}."},
        }

    def save_env_settings(self, anthropic_api_key: str, claude_model: str, claude_use_by_default: bool) -> None:
        values = dict(dotenv_values(self.root / ".env"))
        if anthropic_api_key:
            values["ANTHROPIC_API_KEY"] = anthropic_api_key
        values["CLAUDE_MODEL"] = claude_model
        values["CLAUDE_USE_BY_DEFAULT"] = "true" if claude_use_by_default else "false"
        self.write_env(values)

    def save_contact(self, contact_update: dict[str, str]) -> None:
        path = self.root / "profile" / "contact.yaml"
        data = read_yaml(path, {})
        contact = data.get("contact", {})
        contact.update(contact_update)
        name = contact_update.get("name", "")
        contact["first_name"] = name.split(" ")[0] if name else ""
        contact["last_name"] = name.split(" ")[-1] if name else ""
        data["contact"] = contact
        write_yaml(path, data)

    def save_preferences(
        self,
        *,
        available_from: str,
        logistics: str,
        current_base: str,
        onsite_roles: str,
        preferred_regions: str,
        interests: str,
        minimum_digest_score: int,
    ) -> None:
        path = self.root / "profile" / "preferences.yaml"
        data = read_yaml(path, {})
        data["availability"] = {"available_from": available_from, "logistics": logistics}
        data["location_policy"] = {
            "current_base": current_base,
            "onsite_roles": onsite_roles,
            "preferred_regions": lines_to_list(preferred_regions),
        }
        role_preferences = data.get("role_preferences", {})
        role_preferences["interests"] = lines_to_list(interests)
        data["role_preferences"] = role_preferences
        data["thresholds"] = {"minimum_digest_score": minimum_digest_score}
        write_yaml(path, data)

    def toggle_source(self, index: int, enabled: bool) -> None:
        path = self.root / "sources" / "recruiting-sites.yaml"
        data = read_yaml(path, {"sources": []})
        sources = data.get("sources", [])
        if index < 0 or index >= len(sources):
            raise IndexError("Invalid source index")
        sources[index]["enabled"] = enabled
        write_yaml(path, data)

    def add_source(self, *, name: str, url: str, source_type: str, keywords: str, enabled: bool) -> None:
        path = self.root / "sources" / "recruiting-sites.yaml"
        data = read_yaml(path, {"sources": []})
        entry: dict[str, Any] = {"name": name, "type": source_type, "enabled": enabled}
        if url:
            entry["url"] = url
        keyword_list = lines_to_list(keywords)
        if keyword_list:
            entry["keywords"] = keyword_list
        data.setdefault("sources", []).append(entry)
        write_yaml(path, data)

    def save_setup_file(self, file_key: str, content: str) -> None:
        files = self.setup_files()
        if file_key not in files:
            raise KeyError("Unsupported setup file")
        atomic_write_text(self.root / files[file_key]["path"], content, encoding="utf-8")

    def load_source_entries(self) -> list[dict[str, Any]]:
        data = self.load_yaml_file("sources/recruiting-sites.yaml")
        entries = []
        for index, source in enumerate(data.get("sources", [])):
            item = dict(source)
            item["_index"] = index
            entries.append(item)
        return entries

    def load_yaml_file(self, relative_path: str) -> dict[str, Any]:
        return read_yaml(self.root / relative_path, {})

    def read_text(self, relative_path: str) -> str:
        path = self.root / relative_path
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_env(self, values: dict[str, str]) -> None:
        lines = [f"{key}={value}" for key, value in values.items() if value is not None]
        atomic_write_text(self.root / ".env", "\n".join(lines) + "\n", encoding="utf-8")
