from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.material_service import MaterialService
from job_agent.services.package_index_service import PackageIndexService
from job_agent.services.setup_service import SetupService
from job_agent.web.workflows import AppWorkflowHandler

WEB_DIR = Path(__file__).resolve().parent


def template_context(request: Request) -> dict[str, str]:
    from job_agent.web.runtime import compute_app_version, runtime

    active_draft = workflow_handler().profile.active_draft()
    return {
        "asset_version": runtime.app_version or compute_app_version(runtime.root),
        "profile_unreviewed_draft": bool(active_draft),
        "profile_draft_url": str(active_draft.get("url") or "/setup#cv-profile-draft"),
    }


templates = Jinja2Templates(directory=WEB_DIR / "templates", context_processors=[template_context])
_current_root = ROOT


def current_root() -> Path:
    return _current_root


def package_service() -> PackageIndexService:
    return PackageIndexService(_current_root)


def application_status_store() -> ApplicationStatusStore:
    return ApplicationStatusStore(_current_root)


def material_service() -> MaterialService:
    return MaterialService(_current_root)


def setup_service() -> SetupService:
    return SetupService(_current_root)


def cv_reference_service() -> CvReferenceService:
    return CvReferenceService(_current_root)


def workflow_handler() -> AppWorkflowHandler:
    return AppWorkflowHandler(_current_root)
