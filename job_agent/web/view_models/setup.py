from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from job_agent.config import ROOT
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.setup_service import SetupService
from job_agent.web.constants import CLAUDE_MODELS, TEMPLATE_VARIABLES


def build_setup_view(root: Path = ROOT) -> dict:
    setup = SetupService(root)
    setup.ensure_private_profile()
    return {
        "env": dotenv_values(root / ".env"),
        "files": setup.setup_files(),
        "profile": setup.load_profile_for_setup(),
        "model_options": CLAUDE_MODELS,
        "template_variables": TEMPLATE_VARIABLES,
        "cv_reference": CvReferenceService(root).get_cv_reference(),
    }
