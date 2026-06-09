from __future__ import annotations

from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.io.yaml_store import read_yaml


def build_profile_contract(root: Path = ROOT, cv_reference: dict[str, Any] | None = None) -> dict[str, Any]:
    profile_dir = root / "profile"
    contact_yaml = _dict(read_yaml(profile_dir / "contact.yaml", {}))
    contact = _dict(contact_yaml.get("contact", {}))
    preferences = _dict(read_yaml(profile_dir / "preferences.yaml", {}))
    skills_yaml = _dict(read_yaml(profile_dir / "skills.yaml", {}))
    experience_yaml = _dict(read_yaml(profile_dir / "experience.yaml", {}))
    canonical_cv = _read_text(profile_dir / "canonical-cv.md")
    writing_style = _read_text(profile_dir / "writing-style.md")
    cv_reference = cv_reference or {}

    availability = _dict(preferences.get("availability", {}))
    location_policy = _dict(preferences.get("location_policy", {}))
    role_preferences = _dict(preferences.get("role_preferences", {}))
    thresholds = _dict(preferences.get("thresholds", {}))
    skills = _dict(skills_yaml.get("skills", {}))
    modules = _dict(skills.get("modules", {}))
    experience = _list_of_dicts(experience_yaml.get("experience", []))

    sections = [
        _section(
            "identity",
            "Identity",
            "profile/contact.yaml",
            target_id="profile",
            data_present=bool(contact.get("name") or contact.get("email") or contact.get("title")),
            attention=not bool(contact.get("name") and contact.get("email")),
            badge="Needs basics" if not bool(contact.get("name") and contact.get("email")) else "Has data",
            summary=_summary("Name, title, contact details, and base location.", contact.get("name")),
            signals=[
                _signal("Name", contact.get("name")),
                _signal("Email", contact.get("email")),
                _signal("Location", contact.get("location") or contact.get("city")),
            ],
        ),
        _section(
            "availability",
            "Availability & Constraints",
            "profile/preferences.yaml",
            target_id="preferences",
            data_present=bool(
                availability.get("available_from")
                or location_policy.get("current_base")
                or location_policy.get("onsite_roles")
                or role_preferences.get("preferred_contract_types")
                or role_preferences.get("avoid_contract_types")
            ),
            summary="Human work constraints used by generated materials and profile context.",
            signals=[
                _signal("Available", availability.get("available_from")),
                _signal("Base", location_policy.get("current_base")),
                _signal("Preferred regions", _count(location_policy.get("preferred_regions", []))),
                _signal("Contract preferences", _count(role_preferences.get("preferred_contract_types", []))),
            ],
        ),
        _section(
            "skill_matrix",
            "Skill Matrix",
            "profile/skills.yaml",
            target_id="skill-matrix",
            data_present=bool(skills.get("strongest")),
            summary="Structured skills, module exposure, target roles, and honest caveats.",
            signals=[
                _signal("Strongest skills", _count(skills.get("strongest", []))),
                _signal("Strong modules", _count(modules.get("strong", []))),
                _signal("Experienced modules", _count(modules.get("experienced", []))),
                _signal("Caveats", _count(skills.get("caveats", {}))),
            ],
        ),
        _section(
            "case_studies",
            "Case Studies",
            "profile/experience.yaml",
            target_id="case-studies",
            data_present=bool(experience),
            summary="Structured experience used to choose relevant examples for generated CVs.",
            signals=[
                _signal("Entries", _count(experience)),
                _signal("Keyworded entries", _count([item for item in experience if item.get("keywords")])),
            ],
        ),
        _section(
            "writing_reference",
            "Writing Reference",
            "profile/canonical-cv.md / profile/writing-style.md",
            target_id="writing-reference",
            data_present=bool(canonical_cv.strip() or writing_style.strip()),
            summary="Narrative CV evidence and writing guidance for AI-assisted text.",
            signals=[
                _signal("CV narrative", f"{len(canonical_cv.strip())} chars" if canonical_cv.strip() else ""),
                _signal("Writing style", "Set" if writing_style.strip() else ""),
            ],
        ),
        _section(
            "cv_evidence",
            "CV Evidence",
            "profile/files/reference-cv.*",
            target_id="cv-reference",
            data_present=bool(cv_reference),
            attention=bool(cv_reference.get("extraction_error")),
            badge="Extraction issue" if cv_reference.get("extraction_error") else None,
            summary="Uploaded CV used as import evidence, not as the only profile source of truth.",
            signals=[
                _signal("Reference file", cv_reference.get("filename")),
                _signal("Extracted text", "Available" if cv_reference.get("extracted_text") else ""),
                _signal("Extraction error", cv_reference.get("extraction_error")),
            ],
        ),
    ]

    diagnostics = _diagnostics(
        contact=contact,
        preferences=preferences,
        skills_yaml=skills_yaml,
        experience=experience,
        canonical_cv=canonical_cv,
        writing_style=writing_style,
        cv_reference=cv_reference,
        thresholds=thresholds,
    )

    issue_count = sum(1 for item in diagnostics if item["severity"] in {"warning", "error"})
    info_count = sum(1 for item in diagnostics if item["severity"] == "info")
    data_count = sum(1 for section in sections if section["state"] == "available")

    return {
        "sections": sections,
        "section_count": len(sections),
        "section_ready_count": data_count,
        "section_needs_count": len(sections) - data_count,
        "section_data_count": data_count,
        "section_empty_count": len(sections) - data_count,
        "diagnostics": diagnostics,
        "issue_count": issue_count,
        "info_count": info_count,
        "flow": [
            {"label": "CV evidence", "detail": "Upload and extract source material."},
            {"label": "Structured profile", "detail": "Review identity, constraints, skills, and cases."},
            {"label": "Outputs", "detail": "Job packages, AI prompts, and generated material use those fields."},
        ],
        "status": "attention" if issue_count else "available",
    }


def _diagnostics(
    *,
    contact: dict[str, Any],
    preferences: dict[str, Any],
    skills_yaml: dict[str, Any],
    experience: list[dict[str, Any]],
    canonical_cv: str,
    writing_style: str,
    cv_reference: dict[str, Any],
    thresholds: dict[str, Any],
) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    skills = skills_yaml.get("skills", {})
    role_preferences = preferences.get("role_preferences", {})

    if not contact.get("name") or not contact.get("email"):
        diagnostics.append(
            _diagnostic("warning", "Identity needs basics", "Name and email are needed for generated CVs and form answers.")
        )
    if canonical_cv.strip() and not skills.get("strongest"):
        diagnostics.append(
            _diagnostic(
                "info",
                "CV narrative is not mirrored in skills",
                "The CV text exists, but the structured skill matrix is empty. Deterministic templates cannot reliably pick top skills.",
            )
        )
    if skills.get("strongest") and not canonical_cv.strip():
        diagnostics.append(
            _diagnostic(
                "info",
                "Structured skills lack CV narrative",
                "The skill matrix is filled, but AI-assisted writing has no canonical CV narrative to use as evidence.",
            )
        )
    if not experience:
        diagnostics.append(
            _diagnostic(
                "info",
                "No case studies configured",
                "Generated CVs pick relevant project examples from profile/experience.yaml.",
            )
        )
    if cv_reference.get("extraction_error"):
        diagnostics.append(
            _diagnostic("error", "CV text extraction failed", cv_reference["extraction_error"])
        )
    elif cv_reference and not cv_reference.get("extracted_text"):
        diagnostics.append(
            _diagnostic(
                "warning",
                "CV uploaded without extracted text",
                "The file is available as evidence, but CV-based profile drafting needs extracted text.",
            )
        )
    if thresholds.get("highlight_score") or thresholds.get("ai_evaluation_score"):
        diagnostics.append(
            _diagnostic(
                "info",
                "Advanced thresholds are active",
                "highlight_score and ai_evaluation_score are consumed by runs but are only visible in advanced YAML today.",
            )
        )
    if skills_yaml.get("target_roles"):
        diagnostics.append(
            _diagnostic(
                "info",
                "Target roles are captured as profile context",
                "They are useful for AI context and review, but deterministic scoring is not changed in this profile pass.",
            )
        )
    if role_preferences.get("preferred_contract_types") or role_preferences.get("avoid_contract_types"):
        diagnostics.append(
            _diagnostic(
                "info",
                "Contract preferences are captured",
                "They are part of the worker profile. Scoring integration is intentionally left for the later matchmaker pass.",
            )
        )
    if preferences.get("match_engine"):
        diagnostics.append(
            _diagnostic(
                "info",
                "Manual match settings are separate",
                "Matchmaking settings remain stored under preferences.yaml, but this profile pass does not change scoring behavior.",
            )
        )
    if not writing_style.strip():
        diagnostics.append(
            _diagnostic("info", "Writing style is empty", "AI-assisted writing will still work, but with less style guidance.")
        )
    if not diagnostics:
        diagnostics.append(_diagnostic("ok", "Profile contract looks usable", "No immediate profile consistency issues found."))
    return diagnostics


def _section(
    key: str,
    label: str,
    path: str,
    *,
    target_id: str,
    data_present: bool,
    summary: str,
    signals: list[dict[str, str]],
    attention: bool = False,
    badge: str | None = None,
) -> dict[str, Any]:
    state = "attention" if attention else "available" if data_present else "optional"
    return {
        "key": key,
        "label": label,
        "path": path,
        "target_id": target_id,
        "state": state,
        "badge": badge or ("Has data" if data_present else "Empty"),
        "summary": summary,
        "signals": signals,
    }


def _diagnostic(severity: str, title: str, detail: str) -> dict[str, str]:
    return {"severity": severity, "title": title, "detail": detail}


def _signal(label: str, value: Any) -> dict[str, str]:
    text = str(value or "").strip()
    return {"label": label, "value": text or "Missing"}


def _summary(default: str, value: Any) -> str:
    text = str(value or "").strip()
    return f"{default} Current: {text}." if text else default


def _count(value: Any) -> str:
    count = len([item for item in value.values() if item]) if isinstance(value, dict) else len(value or [])
    return str(count)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
