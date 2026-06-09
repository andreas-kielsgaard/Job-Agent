from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Callable

from dotenv import dotenv_values

from job_agent.config import ROOT, load_profile
from job_agent.io.atomic import atomic_write_text
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.llm import LlmService
from job_agent.models import Job
from job_agent.scoring import match_engine_config_from_profile, normalize_match_engine_config, score_job
from job_agent.services.package_index_service import PackageIndexService

AUTO_CONFIG_TARGETS = (
    "canonical_cv",
    "skills",
    "experience",
    "preferences",
    "match_engine",
)

ProfileDraftProgress = Callable[[str, str, int], None]


def lines_to_list(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def terms_to_list(value: str) -> list[str]:
    return [term.strip() for part in value.splitlines() for term in part.split(",") if term.strip()]


class SetupService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def ensure_private_profile(self) -> None:
        profile = self.root / "profile"
        if not profile.exists() and (self.root / "profile.example").exists():
            shutil.copytree(self.root / "profile.example", profile)

    def load_profile_for_setup(self) -> dict[str, Any]:
        self.ensure_private_profile()
        skills_yaml = self.load_yaml_file("profile/skills.yaml")
        experience_yaml = self.load_yaml_file("profile/experience.yaml")
        return {
            "contact": self.load_yaml_file("profile/contact.yaml").get("contact", {}),
            "preferences": self.load_yaml_file("profile/preferences.yaml"),
            "skills_yaml": skills_yaml,
            "skills": skills_yaml.get("skills", {}) if isinstance(skills_yaml.get("skills", {}), dict) else {},
            "experience_yaml": experience_yaml,
            "experience": experience_yaml.get("experience", [])
            if isinstance(experience_yaml.get("experience", []), list)
            else [],
            "canonical_cv": self.read_text("profile/canonical-cv.md"),
            "writing_style": self.read_text("profile/writing-style.md"),
        }

    def setup_files(self) -> dict[str, dict[str, str]]:
        return {
            "skills": {
                "label": "Skill matrix and caveats",
                "field_id": "profile.skills",
                "path": "profile/skills.yaml",
                "content": self.read_text("profile/skills.yaml"),
                "help": "Structured source of truth for skills, modules, target roles, and honest caveats. Used by generated materials and AI context; match scoring integration is managed separately.",
            },
            "experience": {
                "label": "Case studies",
                "field_id": "profile.experience",
                "path": "profile/experience.yaml",
                "content": self.read_text("profile/experience.yaml"),
                "help": "Structured project evidence. Keywords select the most relevant examples for the at-a-glance CV.",
            },
            "canonical_cv": {
                "label": "CV narrative",
                "field_id": "profile.canonical_cv",
                "path": "profile/canonical-cv.md",
                "content": self.read_text("profile/canonical-cv.md"),
                "help": "Narrative CV evidence for Claude prompts. The structured YAML files remain the source of truth for app behavior.",
            },
            "writing_style": {
                "label": "Writing style",
                "field_id": "profile.writing_style",
                "path": "profile/writing-style.md",
                "content": self.read_text("profile/writing-style.md"),
                "help": "Tone and wording guidance for generated text.",
            },
            "cv_template": {
                "label": "At-a-glance CV template",
                "field_id": "template.cv",
                "path": "templates/at-a-glance-cv.md.j2",
                "content": self.read_text("templates/at-a-glance-cv.md.j2"),
                "help": "Jinja template. Use {{ contact.name }}, {{ top_skills }}, {{ selected_experience }}, etc.",
            },
            "application_template": {
                "label": "Application template",
                "field_id": "template.application",
                "path": "templates/application-letter.md.j2",
                "content": self.read_text("templates/application-letter.md.j2"),
                "help": "Deterministic fallback template used when Claude is disabled or fails.",
            },
            "form_template": {
                "label": "Form answers template",
                "field_id": "template.form",
                "path": "templates/form-answers.md.j2",
                "content": self.read_text("templates/form-answers.md.j2"),
                "help": "Standard form answer package. Do not imply actual form inspection here.",
            },
            "application_prompt": {
                "label": "Claude application prompt",
                "field_id": "prompt.application",
                "path": "prompts/generate_application.md",
                "content": self.read_text("prompts/generate_application.md"),
                "help": "Prompt template for Claude application generation. Variables use Python .format style: {canonical_cv}, {title}, {description}.",
            },
        }

    def load_match_engine_settings(self) -> dict[str, Any]:
        data = self.load_yaml_file("profile/preferences.yaml")
        return normalize_match_engine_config(data.get("match_engine", {}))

    def match_engine_form_model(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = normalize_match_engine_config(settings or self.load_match_engine_settings())
        return {
            **settings,
            "remote_policy_options": [
                {"value": "required", "label": "Require remote/hybrid"},
                {"value": "strong_preference", "label": "Strong preference"},
                {"value": "slight_preference", "label": "Slight preference"},
                {"value": "neutral", "label": "Neutral"},
            ],
            "permanent_policy_options": [
                {"value": "exclude", "label": "Exclude"},
                {"value": "penalize", "label": "Penalize"},
                {"value": "ignore", "label": "Ignore"},
            ],
            "rule_mode_options": [
                {"value": "bonus", "label": "Bonus"},
                {"value": "required", "label": "Required"},
            ],
            "technical_rows": settings["technical_keyword_groups"],
            "module_rows": settings["module_keyword_groups"],
            "contract_rows": settings["contract_keyword_groups"],
        }

    def match_engine_settings_from_form(self, form: Any) -> dict[str, Any]:
        return normalize_match_engine_config(
            {
                "remote_policy": str(form.get("remote_policy", "")),
                "permanent_policy": str(form.get("permanent_policy", "")),
                "permanent_penalty": form.get("permanent_penalty", -25),
                "technical_cap": form.get("technical_cap", 55),
                "module_cap": form.get("module_cap", 25),
                "technical_keyword_groups": self._rules_from_form(form, "technical"),
                "module_keyword_groups": self._rules_from_form(form, "module"),
                "contract_keyword_groups": self._rules_from_form(form, "contract"),
            }
        )

    def save_match_engine_settings_from_form(self, form: Any) -> dict[str, Any]:
        settings = self.match_engine_settings_from_form(form)
        path = self.root / "profile" / "preferences.yaml"
        data = read_yaml(path, {})
        data["match_engine"] = settings
        write_yaml(path, data)
        return settings

    def auto_config_targets_from_form(self, form: Any) -> list[str]:
        return [target for target in AUTO_CONFIG_TARGETS if _truthy(form.get(f"configure_{target}"))]

    def auto_configure_profile_from_cv(
        self,
        cv_text: str,
        targets: list[str],
        progress_callback: ProfileDraftProgress | None = None,
    ) -> dict[str, Any]:
        draft = self.draft_profile_auto_configuration_from_cv(cv_text, targets, progress_callback=progress_callback)
        _profile_draft_progress(progress_callback, "Saving", "Applying selected draft sections to the profile.", 92)
        return self.apply_profile_auto_configuration(draft["data"], draft["targets"])

    def draft_profile_auto_configuration_from_cv(
        self,
        cv_text: str,
        targets: list[str],
        progress_callback: ProfileDraftProgress | None = None,
    ) -> dict[str, Any]:
        targets = [target for target in targets if target in AUTO_CONFIG_TARGETS]
        if not targets:
            raise ValueError("Select at least one profile section to configure.")
        cv_text = cv_text.strip()
        if not cv_text:
            raise ValueError("No extracted CV text is available for auto-configuration.")
        _profile_draft_progress(
            progress_callback,
            "Preparing",
            f"Preparing CV evidence for {len(targets)} selected profile section(s).",
            18,
        )
        llm = LlmService(self.root)
        if not llm.is_configured():
            raise ValueError("Claude is not configured. Add an Anthropic API key in AI Writing first.")
        _profile_draft_progress(
            progress_callback,
            "Calling Claude",
            "Asking Claude to draft structured profile settings from the CV.",
            38,
        )
        completion = llm.complete(
            self._auto_configure_prompt(cv_text, targets),
            max_tokens=3000,
            purpose="profile_auto_configuration",
        )
        _profile_draft_progress(progress_callback, "Parsing draft", "Checking Claude's draft response.", 72)

        def repair_invalid_json(raw_json: str, error: json.JSONDecodeError) -> str:
            _profile_draft_progress(
                progress_callback,
                "Repairing draft",
                "Claude returned malformed JSON; asking it to repair the draft without changing the content.",
                82,
            )
            return llm.complete(
                _json_repair_prompt(raw_json, error),
                max_tokens=4000,
                purpose="profile_auto_configuration_repair",
            ).text

        data = _parse_auto_configuration_json(completion.text, repair_callback=repair_invalid_json)
        _profile_draft_progress(progress_callback, "Preparing preview", "Building the profile draft preview.", 90)
        return {
            "data": data,
            "targets": targets,
            "sections": self.profile_auto_configuration_preview(data, targets),
        }

    def profile_auto_configuration_preview(self, data: dict[str, Any], targets: list[str]) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        if "canonical_cv" in targets:
            canonical = str(data.get("canonical_cv", "")).strip()
            sections.append(
                {
                    "key": "canonical_cv",
                    "label": "CV narrative",
                    "status": "Ready" if canonical else "Missing",
                    "summary": _first_line(canonical) or "No CV narrative was returned.",
                }
            )
        if "skills" in targets:
            skills_yaml = data.get("skills_yaml") if isinstance(data.get("skills_yaml"), dict) else {}
            skills = skills_yaml.get("skills", {}) if isinstance(skills_yaml, dict) else {}
            sections.append(
                {
                    "key": "skills",
                    "label": "Skill matrix and caveats",
                    "status": "Ready" if skills else "Missing",
                    "summary": _list_summary(skills.get("strongest", []), "strong skills"),
                }
            )
        if "experience" in targets:
            experience_yaml = data.get("experience_yaml") if isinstance(data.get("experience_yaml"), dict) else {}
            entries = experience_yaml.get("experience", []) if isinstance(experience_yaml, dict) else []
            sections.append(
                {
                    "key": "experience",
                    "label": "Case studies",
                    "status": "Ready" if entries else "Missing",
                    "summary": f"{len(entries)} experience entries returned.",
                }
            )
        if "preferences" in targets:
            preferences = data.get("preferences_yaml") if isinstance(data.get("preferences_yaml"), dict) else {}
            sections.append(
                {
                    "key": "preferences",
                    "label": "Availability and constraints",
                    "status": "Ready" if preferences else "Missing",
                    "summary": ", ".join(preferences.keys()) if preferences else "No preference fields were returned.",
                }
            )
        if "match_engine" in targets:
            match_engine = data.get("match_engine") if isinstance(data.get("match_engine"), dict) else {}
            sections.append(
                {
                    "key": "match_engine",
                    "label": "Matchmaking settings",
                    "status": "Ready" if match_engine else "Missing",
                    "summary": "Optional scoring draft returned." if match_engine else "No match settings were returned.",
                }
            )
        return sections

    def apply_profile_auto_configuration(self, data: dict[str, Any], targets: list[str]) -> dict[str, Any]:
        applied: list[str] = []
        missing: list[str] = []
        if "canonical_cv" in targets:
            canonical = str(data.get("canonical_cv", "")).strip()
            if canonical:
                atomic_write_text(self.root / "profile" / "canonical-cv.md", canonical + "\n", encoding="utf-8")
                applied.append("canonical CV")
            else:
                missing.append("canonical CV")
        if "skills" in targets:
            skills = data.get("skills_yaml")
            if isinstance(skills, dict):
                write_yaml(self.root / "profile" / "skills.yaml", skills)
                applied.append("skills")
            else:
                missing.append("skills")
        if "experience" in targets:
            experience = data.get("experience_yaml")
            if isinstance(experience, dict):
                write_yaml(self.root / "profile" / "experience.yaml", experience)
                applied.append("experience")
            else:
                missing.append("experience")
        if "preferences" in targets or "match_engine" in targets:
            preferences_path = self.root / "profile" / "preferences.yaml"
            preferences = read_yaml(preferences_path, {})
            if "preferences" in targets:
                patch = data.get("preferences_yaml")
                if isinstance(patch, dict):
                    for key in ["availability", "location_policy", "role_preferences", "thresholds"]:
                        if key in patch:
                            preferences[key] = patch[key]
                    applied.append("preferences")
                else:
                    missing.append("preferences")
            if "match_engine" in targets:
                match_engine = data.get("match_engine")
                if isinstance(match_engine, dict):
                    preferences["match_engine"] = normalize_match_engine_config(match_engine)
                    applied.append("matchmaking settings")
                else:
                    missing.append("matchmaking settings")
            write_yaml(preferences_path, preferences)
        return {"applied": applied, "missing": missing}

    def _auto_configure_prompt(self, cv_text: str, targets: list[str]) -> str:
        current_profile = load_profile(self.root)
        requested = ", ".join(targets)
        default_match_engine = normalize_match_engine_config({})
        return f"""You are configuring a local SAP job matching profile from a CV.

Use only evidence present in the CV. Do not invent employers, dates, languages, rates, locations, or certifications.
Return only valid JSON. Include only keys needed for the requested sections.
Do not wrap the JSON in Markdown. Do not add comments. Do not use trailing commas.
Escape newline characters inside string values as \\n.

Requested sections: {requested}

Output schema:
{{
  "canonical_cv": "Markdown CV narrative evidence when requested.",
  "skills_yaml": {{
    "experience_level": {{"sap_experience": "...", "freelance_status": "..."}},
    "skills": {{
      "strongest": ["..."],
      "modules": {{"strong": ["..."], "experienced": ["..."], "adjacent": ["..."]}},
      "caveats": {{"fiori": "...", "project_management": "..."}}
    }},
    "target_roles": {{"high_match": ["..."], "exploratory_match": ["..."], "lower_match": ["..."]}}
  }},
  "experience_yaml": {{"experience": [{{"company": "...", "role": "...", "highlights": ["..."], "keywords": ["..."]}}]}},
  "preferences_yaml": {{
    "availability": {{"available_from": "...", "logistics": "..."}},
    "location_policy": {{"current_base": "...", "onsite_roles": "...", "preferred_regions": ["..."]}},
    "role_preferences": {{"preferred_contract_types": ["..."], "avoid_contract_types": ["..."], "interests": ["..."]}},
    "thresholds": {{"minimum_digest_score": 45}}
  }},
  "match_engine": {{
    "remote_policy": "required|strong_preference|slight_preference|neutral",
    "permanent_policy": "exclude|penalize|ignore",
    "permanent_penalty": -25,
    "technical_cap": 55,
    "module_cap": 25,
    "technical_keyword_groups": [
      {{"label": "ABAP variants", "terms": ["abap", "abap coding", "abap development"], "score": 22, "mode": "bonus|required"}}
    ],
    "module_keyword_groups": [
      {{"label": "QM", "terms": ["qm", "quality management"], "score": 7, "mode": "bonus|required"}}
    ],
    "contract_keyword_groups": [
      {{"label": "Contract / freelance", "terms": ["contract", "freelance"], "score": 8, "mode": "bonus|required"}}
    ]
  }}
}}

Scoring guidance:
- Points: how much a matched group contributes before the section cap is applied.
- Mode bonus: add points when any alternative term matches.
- Mode required: exclude postings that lack all alternatives for that group.
- Caps are section maximums, not per-keyword points.
- Make match_engine conservative and explainable. Use grouped alternatives, not many duplicate one-term rows.

Current profile JSON:
{json.dumps(_profile_for_prompt(current_profile), ensure_ascii=False, indent=2, default=str)}

Default match engine JSON:
{json.dumps(default_match_engine, ensure_ascii=False, indent=2, default=str)}

CV text:
{cv_text[:20000]}
"""

    def sandbox_input_from_form(self, form: Any) -> dict[str, str]:
        defaults = self.default_sandbox_input()
        return {key: str(form.get(key, defaults[key]) or "") for key in defaults}

    def default_sandbox_input(self) -> dict[str, str]:
        return {
            "title": "SAP ABAP RAP Consultant",
            "company": "Example Client",
            "source": "Scoring Sandbox",
            "url": "",
            "application_url": "",
            "location": "Remote EU",
            "remote": "Remote",
            "rate": "EUR 800/day",
            "contract_duration": "6 months",
            "start_date": "",
            "posted_date": str(date.today()),
            "deadline": "",
            "workload": "Contract",
            "languages": "English",
            "required_skills": "ABAP\nRAP\nCDS\nOData\nGateway",
            "required_modules": "QM",
            "description": "Contract SAP ABAP role with RAP, CDS, OData, SAP Gateway, and S/4HANA delivery.",
        }

    def sandbox_input_from_package(self, job_id: str, run_id: str = "") -> dict[str, str] | None:
        package = PackageIndexService(self.root).find_package(job_id, run_id)
        files = PackageIndexService(self.root).read_package_files(package)
        if not files.get("job"):
            return None
        data = json.loads(files["job"])
        return {
            **self.default_sandbox_input(),
            "title": str(data.get("title", "")),
            "company": str(data.get("company", "")),
            "source": str(data.get("source", "")),
            "url": str(data.get("url", "")),
            "application_url": str(data.get("application_url", "")),
            "location": str(data.get("location", "")),
            "remote": str(data.get("remote", "")),
            "rate": str(data.get("rate", "")),
            "contract_duration": str(data.get("contract_duration", "")),
            "start_date": str(data.get("start_date", "")),
            "posted_date": str(data.get("posted_date", "")),
            "deadline": str(data.get("deadline", "")),
            "workload": str(data.get("workload", "")),
            "languages": "\n".join(data.get("languages", [])),
            "required_skills": "\n".join(data.get("required_skills", [])),
            "required_modules": "\n".join(data.get("required_modules", [])),
            "description": str(data.get("description", "") or data.get("raw_text", "")),
        }

    def score_sandbox_input(
        self,
        sandbox_input: dict[str, str],
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = load_profile(self.root)
        if settings is not None:
            profile["match_engine"] = normalize_match_engine_config(settings)
        else:
            profile["match_engine"] = match_engine_config_from_profile(profile)
        job = self.job_from_sandbox_input(sandbox_input)
        match = score_job(job, profile)
        raw_score = sum(match.components.values())
        return {
            "score": match.total_score,
            "category": match.category,
            "raw_score": raw_score,
            "components": [
                {"name": name, "label": _label_from_key(name), "score": score}
                for name, score in match.components.items()
            ],
            "reasons": match.reasons,
            "concerns": match.concerns,
            "missing_information": match.missing_information,
            "recommended_angle": match.recommended_angle,
            "exclusion_reason": match.exclusion_reason,
            "matched_keywords": match.matched_keywords,
            "job": asdict(job),
        }

    def score_sandbox_form(self, form: Any) -> dict[str, Any]:
        return self.score_sandbox_input(
            self.sandbox_input_from_form(form),
            settings=self.match_engine_settings_from_form(form),
        )

    def job_from_sandbox_input(self, data: dict[str, str]) -> Job:
        return Job(
            title=data.get("title", "").strip() or "Sandbox job posting",
            company=data.get("company", "").strip() or "Unknown",
            source=data.get("source", "").strip() or "Scoring Sandbox",
            url=data.get("url", "").strip(),
            application_url=data.get("application_url", "").strip() or data.get("url", "").strip(),
            location=data.get("location", "").strip() or "Not listed",
            remote=data.get("remote", "").strip() or "Not listed",
            rate=data.get("rate", "").strip() or "Not listed",
            contract_duration=data.get("contract_duration", "").strip() or "Not listed",
            start_date=data.get("start_date", "").strip() or "Not listed",
            posted_date=data.get("posted_date", "").strip() or "Not listed",
            deadline=data.get("deadline", "").strip() or "Not listed",
            workload=data.get("workload", "").strip() or "Not listed",
            languages=terms_to_list(data.get("languages", "")),
            required_languages=terms_to_list(data.get("languages", "")),
            required_skills=terms_to_list(data.get("required_skills", "")),
            required_modules=terms_to_list(data.get("required_modules", "")),
            description=data.get("description", "").strip(),
            source_confidence="manual",
            raw_text=data.get("description", "").strip(),
        )

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
        thresholds = data.get("thresholds", {})
        thresholds["minimum_digest_score"] = minimum_digest_score
        data["thresholds"] = thresholds
        write_yaml(path, data)

    def save_setup_file(self, file_key: str, content: str) -> None:
        files = self.setup_files()
        if file_key not in files:
            raise KeyError("Unsupported setup file")
        atomic_write_text(self.root / files[file_key]["path"], content, encoding="utf-8")

    def load_yaml_file(self, relative_path: str) -> dict[str, Any]:
        return read_yaml(self.root / relative_path, {})

    def read_text(self, relative_path: str) -> str:
        path = self.root / relative_path
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_env(self, values: dict[str, str]) -> None:
        lines = [f"{key}={self._format_env_value(value)}" for key, value in values.items() if value is not None]
        atomic_write_text(self.root / ".env", "\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _format_env_value(value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("Environment values cannot contain newlines.")
        if any(character.isspace() for character in value) or "#" in value or '"' in value:
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return value

    def _rules_from_form(self, form: Any, prefix: str) -> list[dict[str, Any]]:
        labels = form.getlist(f"{prefix}_rule_label")
        terms = form.getlist(f"{prefix}_rule_terms")
        scores = form.getlist(f"{prefix}_rule_score")
        modes = form.getlist(f"{prefix}_rule_mode")
        rules: list[dict[str, Any]] = []
        for index in range(max(len(labels), len(terms), len(scores), len(modes))):
            label = _at(labels, index).strip()
            term_list = terms_to_list(_at(terms, index))
            score = _int_or_default(_at(scores, index), 0)
            mode = _at(modes, index).strip() or "bonus"
            if label and term_list and score > 0:
                rules.append({"label": label, "terms": term_list, "score": score, "mode": mode})
        return rules


def _at(values: list[Any], index: int) -> str:
    return str(values[index]) if index < len(values) else ""


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _label_from_key(value: str) -> str:
    return value.replace("_", " ").title()


def _first_line(value: str) -> str:
    return next((line.strip("# ").strip() for line in value.splitlines() if line.strip()), "")


def _list_summary(values: Any, label: str) -> str:
    items = values if isinstance(values, list) else []
    if not items:
        return f"No {label} were returned."
    preview = ", ".join(str(item) for item in items[:5])
    suffix = "" if len(items) <= 5 else f" + {len(items) - 5} more"
    return f"{len(items)} {label}: {preview}{suffix}."


def _truthy(value: Any) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("AI response did not contain a JSON object.")
    return stripped[start : end + 1]


def _parse_auto_configuration_json(
    text: str,
    *,
    repair_callback: Callable[[str, json.JSONDecodeError], str] | None = None,
) -> dict[str, Any]:
    raw_json = _extract_json(text)
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        if repair_callback is None:
            raise ValueError(f"Claude returned invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.") from exc
        repaired_json = _extract_json(repair_callback(raw_json, exc))
        try:
            data = json.loads(repaired_json)
        except json.JSONDecodeError as repair_exc:
            raise ValueError(
                "Claude returned invalid JSON and the repair attempt also failed: "
                f"{repair_exc.msg} at line {repair_exc.lineno}, column {repair_exc.colno}."
            ) from repair_exc
    if not isinstance(data, dict):
        raise ValueError("Claude returned JSON, but the top-level value was not an object.")
    return data


def _json_repair_prompt(raw_json: str, error: json.JSONDecodeError) -> str:
    return f"""Return only corrected valid JSON.

Fix JSON syntax only. Preserve the same object structure and values. Do not add new profile facts. Do not explain.

Original parser error: {error.msg} at line {error.lineno}, column {error.colno}.

Malformed JSON:
{raw_json}
"""


def _profile_draft_progress(
    progress_callback: ProfileDraftProgress | None,
    stage: str,
    message: str,
    progress_percent: int,
) -> None:
    if progress_callback:
        progress_callback(stage, message, progress_percent)


def _profile_for_prompt(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "availability": profile.get("availability", {}),
        "location_policy": profile.get("location_policy", {}),
        "role_preferences": profile.get("role_preferences", {}),
        "thresholds": profile.get("thresholds", {}),
        "skills": profile.get("skills", {}),
        "target_roles": profile.get("target_roles", {}),
        "experience": profile.get("experience", []),
        "match_engine": profile.get("match_engine", {}),
    }
