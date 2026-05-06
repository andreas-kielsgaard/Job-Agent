from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.run_store import RunStore
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.material_service import MaterialService
from job_agent.services.package_index_service import PackageIndexService
from job_agent.services.setup_service import SetupService

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")


def run_store() -> RunStore:
    return RunStore(ROOT)


def package_service() -> PackageIndexService:
    return PackageIndexService(ROOT)


def application_status_store() -> ApplicationStatusStore:
    return ApplicationStatusStore(ROOT)


def material_service() -> MaterialService:
    return MaterialService(ROOT)


def setup_service() -> SetupService:
    return SetupService(ROOT)


def cv_reference_service() -> CvReferenceService:
    return CvReferenceService(ROOT)
