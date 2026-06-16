from __future__ import annotations

from pathlib import Path

from job_agent.config import ROOT
from job_agent.env import load_env
from job_agent.llm import DEFAULT_CLAUDE_MODEL, model_options, normalize_model
from job_agent.profile_contract import build_profile_contract
from job_agent.services.connector_settings_service import ConnectorSettingsService
from job_agent.services.cv_profile_draft_service import CvProfileDraftService
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.setup_service import SetupService
from job_agent.web.constants import TEMPLATE_VARIABLES


def build_setup_view(root: Path = ROOT) -> dict:
    setup = SetupService(root)
    setup.ensure_private_profile()
    cv_reference = CvReferenceService(root).get_cv_reference()
    cv_drafts = CvProfileDraftService(root)
    cv_profile_draft = cv_drafts.active_draft()
    env = load_env(root)
    return {
        "title": "Setup",
        "env": env,
        "files": setup.setup_files(),
        "profile": setup.load_profile_for_setup(),
        "profile_contract": build_profile_contract(root, cv_reference),
        "profile_editor": setup.profile_editor_model(),
        "match_engine": setup.match_engine_form_model(),
        "ai_policy": setup.ai_policy_form_model(),
        "connector_settings": ConnectorSettingsService(root).load(),
        "configured_claude_model": normalize_model(str(env.get("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL)),
        "model_options": model_options(),
        "template_variables": TEMPLATE_VARIABLES,
        "cv_reference": cv_reference,
        "cv_profile_draft": cv_profile_draft,
        "cv_profile_draft_ready_targets": _ready_draft_targets(cv_profile_draft),
        "cv_applied_sections": cv_drafts.applied_sections(),
    }


def _ready_draft_targets(cv_profile_draft: dict | None) -> set[str]:
    if not cv_profile_draft:
        return set()
    targets = {str(target) for target in cv_profile_draft.get("targets", [])}
    sections = cv_profile_draft.get("sections", [])
    ready_sections = {
        str(section.get("key"))
        for section in sections
        if isinstance(section, dict) and str(section.get("status") or "").lower() == "ready"
    }
    if not ready_sections:
        return set()
    return targets.intersection(ready_sections) or ready_sections.intersection(targets)
