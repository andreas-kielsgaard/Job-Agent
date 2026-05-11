from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.run_store import RunStore
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.material_service import MaterialService
from job_agent.services.package_index_service import PackageIndexService
from job_agent.services.recipe_artifact_service import RecipeArtifactService
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.setup_service import SetupService
from job_agent.services.source_registry_service import SourceRegistryService

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")
_current_root = ROOT


def current_root() -> Path:
    return _current_root


def set_root(root: Path) -> None:
    global _current_root
    _current_root = root
    from job_agent.web.runtime import runtime

    runtime.root = root


def reset_root() -> None:
    set_root(ROOT)


def run_store() -> RunStore:
    return RunStore(_current_root)


def package_service() -> PackageIndexService:
    return PackageIndexService(_current_root)


def application_status_store() -> ApplicationStatusStore:
    return ApplicationStatusStore(_current_root)


def material_service() -> MaterialService:
    return MaterialService(_current_root)


def setup_service() -> SetupService:
    return SetupService(_current_root)


def source_registry_service() -> SourceRegistryService:
    return SourceRegistryService(_current_root)


def execution_source_service() -> ExecutionSourceService:
    return ExecutionSourceService(_current_root)


def recipe_artifact_service() -> RecipeArtifactService:
    return RecipeArtifactService(_current_root)


def recipe_candidate_store() -> RecipeCandidateStore:
    return RecipeCandidateStore(_current_root)


def cv_reference_service() -> CvReferenceService:
    return CvReferenceService(_current_root)
