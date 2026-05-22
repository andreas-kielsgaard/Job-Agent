from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from job_agent.application_status_store import ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.run_store import RunStore
from job_agent.services.approved_recipe_adoption_service import ApprovedRecipeAdoptionService
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.material_service import MaterialService
from job_agent.services.package_index_service import PackageIndexService
from job_agent.services.recipe_artifact_service import RecipeArtifactService
from job_agent.services.recipe_candidate_approval_service import RecipeCandidateApprovalService
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_generation_run_service import RecipeGenerationRunService
from job_agent.services.recipe_generation_status_service import RecipeGenerationStatusService
from job_agent.services.setup_service import SetupService
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_registry_service import SourceRegistryService

WEB_DIR = Path(__file__).resolve().parent


def template_context(request: Request) -> dict[str, str]:
    from job_agent.web.runtime import compute_app_version, runtime

    return {"asset_version": runtime.app_version or compute_app_version(runtime.root)}


templates = Jinja2Templates(directory=WEB_DIR / "templates", context_processors=[template_context])
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


def recipe_candidate_approval_service() -> RecipeCandidateApprovalService:
    return RecipeCandidateApprovalService(_current_root)


def recipe_generation_status_service() -> RecipeGenerationStatusService:
    return RecipeGenerationStatusService(_current_root)


def recipe_generation_run_service() -> RecipeGenerationRunService:
    return RecipeGenerationRunService(_current_root)


def approved_recipe_adoption_service() -> ApprovedRecipeAdoptionService:
    return ApprovedRecipeAdoptionService(_current_root)


def source_execution_readiness_service() -> SourceExecutionReadinessService:
    return SourceExecutionReadinessService(_current_root)


def cv_reference_service() -> CvReferenceService:
    return CvReferenceService(_current_root)
