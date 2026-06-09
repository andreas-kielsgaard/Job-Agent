from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from job_agent.config import ROOT
from job_agent.llm import DEFAULT_CLAUDE_MODEL, model_options, normalize_model
from job_agent.profile_contract import build_profile_contract
from job_agent.services.cv_profile_draft_service import CvProfileDraftService
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.setup_service import SetupService
from job_agent.web.constants import TEMPLATE_VARIABLES


def build_setup_view(root: Path = ROOT) -> dict:
    setup = SetupService(root)
    setup.ensure_private_profile()
    cv_reference = CvReferenceService(root).get_cv_reference()
    cv_profile_draft = CvProfileDraftService(root).active_draft()
    env = dotenv_values(root / ".env")
    return {
        "env": env,
        "files": setup.setup_files(),
        "profile": setup.load_profile_for_setup(),
        "profile_contract": build_profile_contract(root, cv_reference),
        "match_engine": setup.match_engine_form_model(),
        "configured_claude_model": normalize_model(str(env.get("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL)),
        "model_options": model_options(),
        "template_variables": TEMPLATE_VARIABLES,
        "cv_reference": cv_reference,
        "cv_profile_draft": cv_profile_draft,
    }
