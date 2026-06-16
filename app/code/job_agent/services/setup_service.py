from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from job_agent.config import ROOT, load_profile
from job_agent.env import load_env
from job_agent.io.atomic import atomic_write_text
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.llm import LlmService
from job_agent.models import Job
from job_agent.paths import env_file, profile_defaults_dir, profile_dir, resolve_project_path
from job_agent.scoring import (
    match_engine_config_from_profile,
    normalize_ai_review_policy,
    normalize_language_policy,
    normalize_match_engine_config,
    score_job,
)
from job_agent.services.application_examples_service import ApplicationExamplesService
from job_agent.services.package_index_service import PackageIndexService

AUTO_CONFIG_TARGETS = (
    "contact",
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
        profile = profile_dir(self.root)
        defaults = profile_defaults_dir(self.root)
        if not profile.exists() and defaults.exists():
            shutil.copytree(defaults, profile)

    def load_profile_for_setup(self) -> dict[str, Any]:
        self.ensure_private_profile()
        skills_yaml = self.load_yaml_file("profile/skills.yaml")
        experience_yaml = self.load_yaml_file("profile/experience.yaml")
        contact = self.load_yaml_file("profile/contact.yaml").get("contact", {})
        contact = contact if isinstance(contact, dict) else {}
        contact["professional_links"] = _normalize_professional_links(contact.get("professional_links"))
        return {
            "contact": contact,
            "preferences": self.load_yaml_file("profile/preferences.yaml"),
            "skills_yaml": skills_yaml,
            "skills": skills_yaml.get("skills", {}) if isinstance(skills_yaml.get("skills", {}), dict) else {},
            "experience_yaml": experience_yaml,
            "experience": experience_yaml.get("experience", [])
            if isinstance(experience_yaml.get("experience", []), list)
            else [],
            "canonical_cv": self.read_text("profile/canonical-cv.md"),
            "writing_style": self.read_text("profile/writing-style.md"),
            "application_examples": ApplicationExamplesService(self.root).list_examples(),
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
            "application_examples": {
                "label": "Example applications",
                "field_id": "profile.application_examples",
                "path": "profile/application-examples.yaml",
                "content": self.read_text("profile/application-examples.yaml"),
                "help": "Human-edited application examples used as style and positioning context for AI-assisted drafting.",
            },
            "cv_template": {
                "label": "At-a-glance CV template",
                "field_id": "template.cv",
                "path": "templates/at-a-glance-cv.md.j2",
                "content": self.read_text("templates/at-a-glance-cv.md.j2"),
                "help": "Jinja template. Use {{ contact.name }}, {{ top_skills }}, {{ selected_experience }}, etc.",
            },
            "focused_cv_template": {
                "label": "Focused one-page CV template",
                "field_id": "template.focused_cv",
                "path": "templates/focused-cv.md.j2",
                "content": self.read_text("templates/focused-cv.md.j2"),
                "help": "Markdown companion template for the deterministic one-page CV.",
            },
            "focused_cv_html_template": {
                "label": "Focused one-page CV HTML template",
                "field_id": "template.focused_cv_html",
                "path": "templates/focused-cv.html.j2",
                "content": self.read_text("templates/focused-cv.html.j2"),
                "help": "Styled HTML template for the print-ready one-page CV.",
            },
            "focused_cv_tex_template": {
                "label": "Focused one-page CV LaTeX template",
                "field_id": "template.focused_cv_tex",
                "path": "templates/focused-cv.tex.j2",
                "content": self.read_text("templates/focused-cv.tex.j2"),
                "help": "LaTeX source template for the recruiter-facing one-page CV PDF.",
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
            "focused_cv_prompt": {
                "label": "Claude focused CV prompt",
                "field_id": "prompt.focused_cv",
                "path": "prompts/generate_focused_cv.md",
                "content": self.read_text("prompts/generate_focused_cv.md"),
                "help": "Prompt template for evidence-only focused CV content. Variables use Python .format style.",
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
        path = profile_dir(self.root) / "preferences.yaml"
        data = read_yaml(path, {})
        data["match_engine"] = settings
        write_yaml(path, data)
        return settings

    def auto_config_targets_from_form(self, form: Any) -> list[str]:
        return [target for target in AUTO_CONFIG_TARGETS if _truthy(form.get(f"configure_{target}"))]

    def cv_text_with_professional_link_evidence(self, cv_text: str, *, enabled: bool) -> str:
        if not enabled:
            return cv_text
        urls = _professional_urls_from_cv_and_profile(cv_text, load_profile(self.root))
        evidence = _fetch_professional_link_evidence(urls)
        if not evidence:
            return cv_text
        return (
            cv_text.rstrip()
            + "\n\n## Public professional link evidence\n"
            + "\n\n".join(evidence)
            + "\n"
        )

    def auto_configure_profile_from_cv(
        self,
        cv_text: str,
        targets: list[str],
        progress_callback: ProfileDraftProgress | None = None,
        llm_model: str = "",
    ) -> dict[str, Any]:
        draft = self.draft_profile_auto_configuration_from_cv(
            cv_text,
            targets,
            progress_callback=progress_callback,
            llm_model=llm_model,
        )
        _profile_draft_progress(progress_callback, "Saving", "Applying selected draft sections to the profile.", 92)
        return self.apply_profile_auto_configuration(draft["data"], draft["targets"])

    def draft_profile_auto_configuration_from_cv(
        self,
        cv_text: str,
        targets: list[str],
        progress_callback: ProfileDraftProgress | None = None,
        llm_model: str = "",
    ) -> dict[str, Any]:
        prompt = self.profile_auto_configuration_prompt(cv_text, targets, progress_callback=progress_callback)
        targets = [target for target in targets if target in AUTO_CONFIG_TARGETS]
        llm = LlmService(self.root)
        if not llm.is_configured():
            raise ValueError("Claude is not configured. Add an Anthropic API key in AI Review & Writing first.")
        _profile_draft_progress(
            progress_callback,
            "Calling Claude",
            "Asking Claude to draft structured profile settings from the CV.",
            38,
        )
        completion = llm.complete(
            prompt,
            max_tokens=3000,
            purpose="profile_auto_configuration",
            model=llm_model,
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
                model=llm_model,
            ).text

        data = _parse_auto_configuration_json(completion.text, repair_callback=repair_invalid_json)
        _profile_draft_progress(progress_callback, "Preparing preview", "Building the profile draft preview.", 90)
        return {
            "data": data,
            "targets": targets,
            "sections": self.profile_auto_configuration_preview(data, targets),
        }

    def profile_auto_configuration_prompt(
        self,
        cv_text: str,
        targets: list[str],
        progress_callback: ProfileDraftProgress | None = None,
    ) -> str:
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
        return self._auto_configure_prompt(cv_text, targets)

    def draft_profile_auto_configuration_from_response(
        self,
        response_text: str,
        targets: list[str],
    ) -> dict[str, Any]:
        targets = [target for target in targets if target in AUTO_CONFIG_TARGETS]
        if not targets:
            raise ValueError("Select at least one profile section to configure.")
        data = _parse_auto_configuration_json(response_text)
        return {
            "data": data,
            "targets": targets,
            "sections": self.profile_auto_configuration_preview(data, targets),
        }

    def profile_auto_configuration_preview(self, data: dict[str, Any], targets: list[str]) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        if "contact" in targets:
            contact = _contact_patch_from_auto_config(data)
            ready_values = [
                contact.get("name"),
                contact.get("title"),
                contact.get("email"),
                contact.get("phone"),
                contact.get("location"),
                contact.get("linkedin"),
                contact.get("professional_links"),
            ]
            sections.append(
                {
                    "key": "contact",
                    "label": "Profile basics",
                    "status": "Ready" if _has_any_value(ready_values) else "Missing",
                    "summary": _contact_summary(contact) or "No profile basics were returned.",
                }
            )
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
                    "summary": "Optional scoring draft returned."
                    if match_engine
                    else "No match settings were returned.",
                }
            )
        return sections

    def apply_profile_auto_configuration(self, data: dict[str, Any], targets: list[str]) -> dict[str, Any]:
        applied: list[str] = []
        applied_targets: list[str] = []
        missing: list[str] = []
        if "contact" in targets:
            contact = _contact_patch_from_auto_config(data)
            if _has_any_value(contact):
                self.save_contact(contact)
                applied.append("profile basics")
                applied_targets.append("contact")
            else:
                missing.append("profile basics")
        if "canonical_cv" in targets:
            canonical = str(data.get("canonical_cv", "")).strip()
            if canonical:
                atomic_write_text(profile_dir(self.root) / "canonical-cv.md", canonical + "\n", encoding="utf-8")
                applied.append("canonical CV")
                applied_targets.append("canonical_cv")
            else:
                missing.append("canonical CV")
        if "skills" in targets:
            skills = data.get("skills_yaml")
            if isinstance(skills, dict):
                write_yaml(profile_dir(self.root) / "skills.yaml", skills)
                applied.append("skills")
                applied_targets.append("skills")
            else:
                missing.append("skills")
        if "experience" in targets:
            experience = data.get("experience_yaml")
            if isinstance(experience, dict):
                write_yaml(profile_dir(self.root) / "experience.yaml", experience)
                applied.append("experience")
                applied_targets.append("experience")
            else:
                missing.append("experience")
        if "preferences" in targets or "match_engine" in targets:
            preferences_path = profile_dir(self.root) / "preferences.yaml"
            preferences = read_yaml(preferences_path, {})
            if "preferences" in targets:
                patch = data.get("preferences_yaml")
                if isinstance(patch, dict):
                    for key in [
                        "availability",
                        "location_policy",
                        "role_preferences",
                        "thresholds",
                        "match_review",
                        "ai_review_policy",
                        "language_policy",
                        "highlighting",
                    ]:
                        if key in patch:
                            preferences[key] = patch[key]
                    applied.append("preferences")
                    applied_targets.append("preferences")
                else:
                    missing.append("preferences")
            if "match_engine" in targets:
                match_engine = data.get("match_engine")
                if isinstance(match_engine, dict):
                    preferences["match_engine"] = normalize_match_engine_config(match_engine)
                    applied.append("matchmaking settings")
                    applied_targets.append("match_engine")
                else:
                    missing.append("matchmaking settings")
            write_yaml(preferences_path, preferences)
        return {"applied": applied, "applied_targets": applied_targets, "missing": missing}

    def _auto_configure_prompt(self, cv_text: str, targets: list[str]) -> str:
        current_profile = load_profile(self.root)
        requested = ", ".join(targets)
        default_match_engine = normalize_match_engine_config({})
        return f"""You are configuring a local job matching profile from a CV.

Use only evidence present in the CV. Do not invent employers, dates, languages, rates, locations, or certifications.
Return only valid JSON. Include only keys needed for the requested sections.
Do not wrap the JSON in Markdown. Do not add comments. Do not use trailing commas.
Escape newline characters inside string values as \\n.

Requested sections: {requested}

Output schema:
{{
  "contact_yaml": {{
    "contact": {{
      "name": "...",
      "title": "...",
      "phone": "...",
      "email": "...",
      "linkedin": "https://...",
      "location": "City, country",
      "city": "...",
      "country": "...",
      "professional_links": [
        {{"label": "Portfolio", "url": "https://..."}}
      ]
    }}
  }},
  "canonical_cv": "Markdown CV narrative evidence when requested.",
  "skills_yaml": {{
    "experience_level": {{"years_experience": "...", "current_status": "..."}},
    "skills": {{
      "strongest": ["..."],
      "modules": {{"strong": ["..."], "experienced": ["..."], "adjacent": ["..."]}},
      "caveats": {{"example_caveat_key": "Honest limitation or positioning note."}}
    }},
    "target_roles": {{"high_match": ["..."], "exploratory_match": ["..."], "lower_match": ["..."]}}
  }},
  "experience_yaml": {{"experience": [{{"company": "...", "role": "...", "highlights": ["..."], "keywords": ["..."]}}]}},
  "preferences_yaml": {{
    "availability": {{"available_from": "...", "logistics": "..."}},
    "location_policy": {{"current_base": "...", "onsite_roles": "...", "preferred_regions": ["..."]}},
    "role_preferences": {{"preferred_contract_types": ["..."], "avoid_contract_types": ["..."], "interests": ["..."]}},
    "thresholds": {{"minimum_digest_score": 45}},
    "match_review": {{"caveat_rules": [
      {{"id": "example_review", "label": "Example review trigger", "terms": ["..."], "caveat_key": "example_caveat_key", "ai_review": true}}
    ]}},
    "ai_review_policy": {{
      "min_score": 35,
      "evaluate_categories": ["strong", "exploratory"],
      "trigger_on_highlights": true,
      "trigger_on_review_triggers": true,
      "trigger_on_low_source_confidence": true,
      "evaluate_excluded_with_triggers": false
    }},
    "language_policy": {{"acceptable": ["..."], "fluent": ["..."], "exclude_if_mandatory_unmatched": true}},
    "highlighting": {{"core_match_groups": ["..."], "min_core_matches": 3, "high_rate_threshold": 700}}
  }},
  "match_engine": {{
    "remote_policy": "required|strong_preference|slight_preference|neutral",
    "permanent_policy": "exclude|penalize|ignore",
    "permanent_penalty": -25,
    "technical_cap": 55,
    "module_cap": 25,
    "technical_keyword_groups": [
      {{"label": "Primary skill variants", "terms": ["primary skill", "alternate wording"], "score": 22, "mode": "bonus|required"}}
    ],
    "module_keyword_groups": [
      {{"label": "Domain or specialization", "terms": ["domain term"], "score": 7, "mode": "bonus|required"}}
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
        profile = load_profile(self.root)
        target_roles = profile.get("target_roles", {})
        high_match_roles = target_roles.get("high_match", []) if isinstance(target_roles, dict) else []
        title = str((high_match_roles or [profile.get("contact", {}).get("title") or "Sample Role"])[0])
        skills = profile.get("skills", {}).get("strongest", [])
        required_skills = "\n".join(str(skill) for skill in skills[:5]) or "Primary skill\nRelated skill"
        return {
            "title": title,
            "company": "Example Client",
            "source": "Scoring Sandbox",
            "url": "",
            "application_url": "",
            "location": "Remote",
            "remote": "Remote",
            "rate": "Not listed",
            "contract_duration": "6 months",
            "start_date": "",
            "posted_date": str(date.today()),
            "deadline": "",
            "workload": "Contract",
            "languages": "English",
            "required_skills": required_skills,
            "required_modules": "",
            "description": "Sample posting text with the role's most relevant skills, responsibilities, and constraints.",
        }

    def profile_editor_model(self) -> dict[str, Any]:
        skills_yaml = self.load_yaml_file("profile/skills.yaml")
        preferences = self.load_yaml_file("profile/preferences.yaml")
        experience_yaml = self.load_yaml_file("profile/experience.yaml")
        skills = skills_yaml.get("skills", {}) if isinstance(skills_yaml.get("skills"), dict) else {}
        modules = skills.get("modules", {}) if isinstance(skills.get("modules"), dict) else {}
        match_engine = normalize_match_engine_config(preferences.get("match_engine", {}))
        technical_by_label = _rules_by_label(match_engine["technical_keyword_groups"])
        module_by_label = _rules_by_label(match_engine["module_keyword_groups"])
        caveat_rules = _caveat_rules_by_key(preferences.get("match_review", {}))
        target_roles = skills_yaml.get("target_roles", {}) if isinstance(skills_yaml.get("target_roles"), dict) else {}
        role_aliases = (
            skills_yaml.get("target_role_aliases", {})
            if isinstance(skills_yaml.get("target_role_aliases"), dict)
            else {}
        )

        return {
            "skills": [
                _editable_match_item(name, technical_by_label.get(_label_key(name)), default_score=20)
                for name in _list(skills.get("strongest", []))
            ],
            "modules": [
                {
                    **_editable_match_item(name, module_by_label.get(_label_key(name)), default_score=7),
                    "lane": lane,
                }
                for lane in ["strong", "experienced", "adjacent"]
                for name in _list(modules.get(lane, []))
            ],
            "target_roles": [
                {"bucket": bucket, "name": name, "aliases": _list(role_aliases.get(name, []))}
                for bucket in ["high_match", "exploratory_match", "lower_match"]
                for name in _list(target_roles.get(bucket, []))
            ],
            "caveats": [
                {
                    "key": key,
                    "text": text,
                    "terms": _list(caveat_rules.get(key, {}).get("terms", [])),
                    "ai_review": caveat_rules.get(key, {}).get("ai_review", True),
                }
                for key, text in _dict(skills.get("caveats", {})).items()
            ],
            "case_studies": _list(experience_yaml.get("experience", [])),
            "application_examples": ApplicationExamplesService(self.root).list_examples(),
        }

    def ai_policy_form_model(self) -> dict[str, Any]:
        preferences = self.load_yaml_file("profile/preferences.yaml")
        policy = normalize_ai_review_policy(preferences)
        language = normalize_language_policy(preferences)
        highlighting = preferences.get("highlighting", {}) if isinstance(preferences.get("highlighting"), dict) else {}
        thresholds = preferences.get("thresholds", {}) if isinstance(preferences.get("thresholds"), dict) else {}
        return {
            "ai_review_policy": policy,
            "language_policy": language,
            "highlighting": {
                "core_match_groups": _list(highlighting.get("core_match_groups", [])),
                "min_core_matches": _int_or_default(highlighting.get("min_core_matches"), 3),
                "high_rate_threshold": _int_or_default(highlighting.get("high_rate_threshold"), 700),
            },
            "minimum_digest_score": _int_or_default(thresholds.get("minimum_digest_score"), 45),
            "category_options": [
                {"value": "strong", "label": "Strong"},
                {"value": "exploratory", "label": "Exploratory"},
                {"value": "weak", "label": "Weak"},
                {"value": "excluded", "label": "Excluded"},
            ],
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
            "review_triggers": match.review_triggers,
            "review_trigger_labels": match.review_trigger_labels,
            "deterministic_confidence": match.deterministic_confidence,
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
        values = load_env(self.root)
        if anthropic_api_key:
            values["ANTHROPIC_API_KEY"] = anthropic_api_key
        values["CLAUDE_MODEL"] = claude_model
        values["CLAUDE_USE_BY_DEFAULT"] = "true" if claude_use_by_default else "false"
        self.write_env(values)

    def save_contact(self, contact_update: dict[str, Any]) -> None:
        path = profile_dir(self.root) / "contact.yaml"
        data = read_yaml(path, {})
        contact = data.get("contact", {})
        contact_update = dict(contact_update)
        if "professional_links" in contact_update:
            contact_update["professional_links"] = _normalize_professional_links(
                contact_update.get("professional_links")
            )
        contact.update(contact_update)
        name = contact_update.get("name", "")
        if name:
            contact["first_name"] = name.split(" ")[0]
            contact["last_name"] = name.split(" ")[-1]
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
        minimum_digest_score: int | None = None,
    ) -> None:
        path = profile_dir(self.root) / "preferences.yaml"
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
        if minimum_digest_score is not None:
            thresholds = data.get("thresholds", {})
            thresholds["minimum_digest_score"] = minimum_digest_score
            data["thresholds"] = thresholds
        write_yaml(path, data)

    def save_run_inclusion(self, minimum_digest_score: int) -> None:
        path = profile_dir(self.root) / "preferences.yaml"
        data = read_yaml(path, {})
        thresholds = data.get("thresholds", {})
        thresholds["minimum_digest_score"] = minimum_digest_score
        data["thresholds"] = thresholds
        write_yaml(path, data)

    def save_runtime_settings(self, max_parallel_sources: int) -> None:
        path = profile_dir(self.root) / "preferences.yaml"
        data = read_yaml(path, {})
        runtime = data.get("runtime", {}) if isinstance(data.get("runtime", {}), dict) else {}
        runtime["max_parallel_sources"] = max(1, int(max_parallel_sources or 10))
        data["runtime"] = runtime
        write_yaml(path, data)

    def save_writing_reference(self, canonical_cv: str | None = None, writing_style: str | None = None) -> None:
        if canonical_cv is not None:
            atomic_write_text(profile_dir(self.root) / "canonical-cv.md", canonical_cv.strip() + "\n", encoding="utf-8")
        if writing_style is not None:
            atomic_write_text(
                profile_dir(self.root) / "writing-style.md", writing_style.strip() + "\n", encoding="utf-8"
            )

    def save_skill_matrix_from_form(self, form: Any) -> None:
        skills_path = profile_dir(self.root) / "skills.yaml"
        preferences_path = profile_dir(self.root) / "preferences.yaml"
        skills_yaml = read_yaml(skills_path, {})
        preferences = read_yaml(preferences_path, {})
        old_skills = _list(_dict(skills_yaml.get("skills", {})).get("strongest", []))
        old_modules = [
            item
            for lane in ["strong", "experienced", "adjacent"]
            for item in _list(_dict(_dict(skills_yaml.get("skills", {})).get("modules", {})).get(lane, []))
        ]
        old_caveats = set(_dict(_dict(skills_yaml.get("skills", {})).get("caveats", {})).keys())

        skill_rows = _parallel_rows(form, ["skill_name", "skill_terms", "skill_score", "skill_mode"])
        module_rows = _parallel_rows(
            form, ["module_lane", "module_name", "module_terms", "module_score", "module_mode"]
        )
        role_rows = _parallel_rows(form, ["role_bucket", "role_name", "role_aliases"])
        caveat_rows = _parallel_rows(form, ["caveat_key", "caveat_text", "caveat_terms", "caveat_ai_review"])
        has_skill_match_fields = any(_form_has_key(form, key) for key in ["skill_terms", "skill_score", "skill_mode"])
        has_module_match_fields = any(
            _form_has_key(form, key) for key in ["module_terms", "module_score", "module_mode"]
        )
        has_role_alias_fields = _form_has_key(form, "role_aliases")
        has_caveat_trigger_fields = any(_form_has_key(form, key) for key in ["caveat_terms", "caveat_ai_review"])

        skills = _dict(skills_yaml.get("skills", {}))
        skill_names = [row["skill_name"].strip() for row in skill_rows if row["skill_name"].strip()]
        skills["strongest"] = skill_names
        modules = {"strong": [], "experienced": [], "adjacent": []}
        for row in module_rows:
            name = row["module_name"].strip()
            lane = row["module_lane"].strip() if row["module_lane"].strip() in modules else "experienced"
            if name:
                modules[lane].append(name)
        skills["modules"] = modules
        caveats: dict[str, str] = {}
        for row in caveat_rows:
            text = row["caveat_text"].strip()
            key = row["caveat_key"].strip() or _slug(text)
            if key and text:
                caveats[key] = text
        skills["caveats"] = caveats
        skills_yaml["skills"] = skills

        target_roles = {"high_match": [], "exploratory_match": [], "lower_match": []}
        existing_aliases = _dict(skills_yaml.get("target_role_aliases", {}))
        role_aliases: dict[str, list[str]] = {}
        for row in role_rows:
            name = row["role_name"].strip()
            bucket = row["role_bucket"].strip() if row["role_bucket"].strip() in target_roles else "high_match"
            if name:
                target_roles[bucket].append(name)
                aliases = (
                    terms_to_list(row["role_aliases"]) if has_role_alias_fields else _list(existing_aliases.get(name))
                )
                if aliases:
                    role_aliases[name] = aliases
        skills_yaml["target_roles"] = target_roles
        skills_yaml["target_role_aliases"] = role_aliases

        preferences_changed = False
        if has_skill_match_fields or has_module_match_fields:
            match_engine = normalize_match_engine_config(preferences.get("match_engine", {}))
            if has_skill_match_fields:
                match_engine["technical_keyword_groups"] = _sync_labeled_rules(
                    match_engine["technical_keyword_groups"],
                    old_skills,
                    [
                        _rule_from_row(row["skill_name"], row["skill_terms"], row["skill_score"], row["skill_mode"], 20)
                        for row in skill_rows
                    ],
                )
            if has_module_match_fields:
                match_engine["module_keyword_groups"] = _sync_labeled_rules(
                    match_engine["module_keyword_groups"],
                    old_modules,
                    [
                        _rule_from_row(
                            row["module_name"], row["module_terms"], row["module_score"], row["module_mode"], 7
                        )
                        for row in module_rows
                    ],
                )
            preferences["match_engine"] = match_engine
            preferences_changed = True

        if has_caveat_trigger_fields:
            preferences["match_review"] = {
                "caveat_rules": _sync_caveat_rules(
                    _dict(preferences.get("match_review", {})).get("caveat_rules", []),
                    old_caveats,
                    caveat_rows,
                )
            }
            preferences_changed = True
        else:
            current_caveats = set(caveats.keys())
            match_review = _dict(preferences.get("match_review", {}))
            caveat_rules = _list(match_review.get("caveat_rules", []))
            filtered_rules = [
                rule
                for rule in caveat_rules
                if not isinstance(rule, dict)
                or not str(rule.get("caveat_key") or "").strip()
                or str(rule.get("caveat_key") or "").strip() in current_caveats
            ]
            if filtered_rules != caveat_rules:
                updated_review = dict(match_review)
                updated_review["caveat_rules"] = filtered_rules
                preferences["match_review"] = updated_review
                preferences_changed = True

        write_yaml(skills_path, skills_yaml)
        if preferences_changed:
            write_yaml(preferences_path, preferences)

    def save_case_studies_from_form(self, form: Any) -> None:
        rows = _parallel_rows(
            form,
            [
                "case_company",
                "case_role",
                "case_highlights",
                "case_keywords",
                "case_linked_skills",
                "case_linked_modules",
                "case_linked_roles",
            ],
        )
        entries = []
        for row in rows:
            if not any(value.strip() for value in row.values()):
                continue
            entries.append(
                {
                    "company": row["case_company"].strip() or "Experience entry",
                    "role": row["case_role"].strip(),
                    "highlights": lines_to_list(row["case_highlights"]),
                    "keywords": terms_to_list(row["case_keywords"]),
                    "linked_skills": terms_to_list(row["case_linked_skills"]),
                    "linked_modules": terms_to_list(row["case_linked_modules"]),
                    "linked_roles": terms_to_list(row["case_linked_roles"]),
                }
            )
        write_yaml(profile_dir(self.root) / "experience.yaml", {"experience": entries})

    def save_application_examples_from_form(self, form: Any) -> None:
        rows = _parallel_rows(
            form,
            [
                "example_id",
                "example_label",
                "example_application_text",
                "example_job_title",
                "example_company",
                "example_url",
                "example_linked_skills",
                "example_linked_modules",
                "example_linked_roles",
                "example_notes",
            ],
        )
        ApplicationExamplesService(self.root).upsert_from_form_rows(
            [
                {
                    "id": row["example_id"],
                    "label": row["example_label"],
                    "application_text": row["example_application_text"],
                    "linked_job": {
                        "title": row["example_job_title"],
                        "company": row["example_company"],
                        "url": row["example_url"],
                    },
                    "linked_skills": terms_to_list(row["example_linked_skills"]),
                    "linked_modules": terms_to_list(row["example_linked_modules"]),
                    "linked_roles": terms_to_list(row["example_linked_roles"]),
                    "notes": row["example_notes"],
                }
                for row in rows
            ]
        )

    def save_ai_policy_from_form(self, form: Any) -> None:
        path = profile_dir(self.root) / "preferences.yaml"
        data = read_yaml(path, {})
        data["ai_review_policy"] = {
            "min_score": _int_or_default(form.get("ai_min_score"), 35),
            "evaluate_categories": _list(form.getlist("evaluate_category")),
            "trigger_on_highlights": _truthy(form.get("trigger_on_highlights")),
            "trigger_on_review_triggers": _truthy(form.get("trigger_on_review_triggers")),
            "trigger_on_low_source_confidence": _truthy(form.get("trigger_on_low_source_confidence")),
            "evaluate_excluded_with_triggers": _truthy(form.get("evaluate_excluded_with_triggers")),
        }
        data["language_policy"] = {
            "acceptable": terms_to_list(str(form.get("acceptable_languages", ""))),
            "fluent": terms_to_list(str(form.get("fluent_languages", ""))),
            "exclude_if_mandatory_unmatched": _truthy(form.get("exclude_if_mandatory_unmatched")),
            "penalty": min(0, _int_or_default(form.get("language_penalty"), -25)),
        }
        data["highlighting"] = {
            "core_match_groups": terms_to_list(str(form.get("core_match_groups", ""))),
            "min_core_matches": _int_or_default(form.get("min_core_matches"), 3),
            "high_rate_threshold": _int_or_default(form.get("high_rate_threshold"), 700),
        }
        write_yaml(path, data)

    def save_setup_file(self, file_key: str, content: str) -> None:
        files = self.setup_files()
        if file_key not in files:
            raise KeyError("Unsupported setup file")
        atomic_write_text(resolve_project_path(self.root, files[file_key]["path"]), content, encoding="utf-8")

    def load_yaml_file(self, relative_path: str) -> dict[str, Any]:
        return read_yaml(resolve_project_path(self.root, relative_path), {})

    def read_text(self, relative_path: str) -> str:
        path = resolve_project_path(self.root, relative_path)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_env(self, values: dict[str, str]) -> None:
        lines = [f"{key}={self._format_env_value(value)}" for key, value in values.items() if value is not None]
        atomic_write_text(env_file(self.root), "\n".join(lines) + "\n", encoding="utf-8")

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


def _contact_patch_from_auto_config(data: dict[str, Any]) -> dict[str, Any]:
    contact_yaml = data.get("contact_yaml") if isinstance(data.get("contact_yaml"), dict) else {}
    contact = contact_yaml.get("contact") if isinstance(contact_yaml.get("contact"), dict) else data.get("contact")
    if not isinstance(contact, dict):
        return {}
    allowed = {
        "name",
        "title",
        "phone",
        "email",
        "linkedin",
        "location",
        "address",
        "post_code",
        "city",
        "country",
        "kommune",
    }
    patch = {key: str(contact.get(key) or "").strip() for key in allowed if str(contact.get(key) or "").strip()}
    links = _normalize_professional_links(contact.get("professional_links"))
    linkedin = patch.get("linkedin")
    if linkedin and not any(link["url"].lower() == linkedin.lower() for link in links):
        links.insert(0, {"label": "LinkedIn", "url": linkedin})
    if links:
        patch["professional_links"] = links
    return patch


def _contact_summary(contact: dict[str, Any]) -> str:
    parts = []
    for key in ["name", "title", "email", "location"]:
        value = str(contact.get(key) or "").strip()
        if value:
            parts.append(value)
    links = _normalize_professional_links(contact.get("professional_links"))
    if links:
        parts.append(f"{len(links)} professional link(s)")
    return ", ".join(parts)


def _normalize_professional_links(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not _is_public_http_url(url):
            continue
        normalized = url.rstrip("/")
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        label = str(item.get("label") or "").strip() or _label_from_url(normalized)
        links.append({"label": label, "url": normalized})
    return links


def _professional_urls_from_cv_and_profile(cv_text: str, profile: dict[str, Any]) -> list[str]:
    urls = [match.rstrip(".,);]") for match in re.findall(r"https?://[^\s<>()\"']+", cv_text or "")]
    contact = profile.get("contact") if isinstance(profile.get("contact"), dict) else {}
    if isinstance(contact, dict):
        urls.append(str(contact.get("linkedin") or ""))
        for link in _normalize_professional_links(contact.get("professional_links")):
            urls.append(link["url"])
    result: list[str] = []
    seen: set[str] = set()
    for url in urls:
        url = str(url or "").strip()
        if not _is_public_http_url(url):
            continue
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(url.rstrip("/"))
        if len(result) >= 4:
            break
    return result


def _fetch_professional_link_evidence(urls: list[str]) -> list[str]:
    evidence: list[str] = []
    for url in urls:
        try:
            response = requests.get(
                url,
                timeout=8,
                headers={"User-Agent": "Job-Agent profile setup (explicit user-provided public link)"},
            )
            response.raise_for_status()
        except requests.RequestException:
            continue
        text = _html_to_text(response.text)
        if text:
            evidence.append(f"Source: {url}\n{text[:2500].strip()}")
    return evidence


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_public_http_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    hostname = (parsed.hostname or "").lower()
    if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".local"):
        return False
    return not (
        hostname.startswith("10.")
        or hostname.startswith("192.168.")
        or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", hostname)
    )


def _label_from_url(value: str) -> str:
    host = (urlparse(value).hostname or "Link").removeprefix("www.")
    if "linkedin." in host:
        return "LinkedIn"
    if "github." in host:
        return "GitHub"
    if "substack." in host:
        return "Substack"
    return host.split(".")[0].replace("-", " ").title() or "Link"


def _has_any_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_any_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_any_value(item) for item in value)
    return bool(str(value or "").strip())


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _rules_by_label(rules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_label_key(rule.get("label")): rule for rule in rules if str(rule.get("label") or "").strip()}


def _editable_match_item(label: Any, rule: dict[str, Any] | None, default_score: int) -> dict[str, Any]:
    rule = rule or {}
    return {
        "name": str(label or "").strip(),
        "terms": _list(rule.get("terms", [])),
        "score": _int_or_default(rule.get("score"), default_score),
        "mode": str(rule.get("mode") or "bonus"),
    }


def _caveat_rules_by_key(match_review: Any) -> dict[str, dict[str, Any]]:
    rules = _dict(match_review).get("caveat_rules", [])
    return {
        str(rule.get("caveat_key") or "").strip(): rule
        for rule in _list(rules)
        if isinstance(rule, dict) and str(rule.get("caveat_key") or "").strip()
    }


def _parallel_rows(form: Any, keys: list[str]) -> list[dict[str, str]]:
    values = {key: [str(value) for value in form.getlist(key)] for key in keys}
    count = max([len(items) for items in values.values()] or [0])
    return [{key: _at(values[key], index) for key in keys} for index in range(count)]


def _form_has_key(form: Any, key: str) -> bool:
    try:
        if key in form:
            return True
    except TypeError:
        pass
    try:
        return bool(form.getlist(key))
    except AttributeError:
        return False


def _label_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _sync_labeled_rules(
    existing_rules: list[dict[str, Any]],
    old_labels: list[str],
    submitted_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    old_label_keys = {_label_key(label) for label in old_labels if str(label).strip()}
    new_rules = [rule for rule in submitted_rules if rule.get("label") and rule.get("terms")]
    new_label_keys = {_label_key(rule["label"]) for rule in new_rules}
    preserved = [
        rule
        for rule in existing_rules
        if _label_key(rule.get("label")) not in old_label_keys and _label_key(rule.get("label")) not in new_label_keys
    ]
    return new_rules + preserved


def _rule_from_row(label: str, terms: str, score: str, mode: str, default_score: int) -> dict[str, Any]:
    label = label.strip()
    term_list = terms_to_list(terms) or ([label] if label else [])
    score_value = _int_or_default(score, default_score)
    mode_value = mode.strip() if mode.strip() in {"bonus", "required"} else "bonus"
    return {"label": label, "terms": term_list, "score": score_value, "mode": mode_value}


def _sync_caveat_rules(
    existing_rules: Any, old_caveat_keys: set[str], rows: list[dict[str, str]]
) -> list[dict[str, Any]]:
    existing = [rule for rule in _list(existing_rules) if isinstance(rule, dict)]
    preserved = [
        rule
        for rule in existing
        if str(rule.get("caveat_key") or "").strip() not in old_caveat_keys
        and not str(rule.get("id") or "").startswith("caveat_")
    ]
    submitted = []
    for row in rows:
        key = row["caveat_key"].strip() or _slug(row["caveat_text"])
        terms = terms_to_list(row["caveat_terms"])
        if not key or not terms:
            continue
        submitted.append(
            {
                "id": f"caveat_{key}",
                "label": key.replace("_", " ").title(),
                "terms": terms,
                "caveat_key": key,
                "ai_review": row["caveat_ai_review"].strip().lower() != "false",
            }
        )
    return preserved + submitted


def _slug(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:60] or "caveat"


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
            raise ValueError(
                f"Claude returned invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}."
            ) from exc
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
        "application_examples": profile.get("application_examples", []),
        "match_engine": profile.get("match_engine", {}),
    }
