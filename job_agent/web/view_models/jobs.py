from __future__ import annotations

from pathlib import Path

from job_agent.application_status_store import APPLICATION_STATUSES, ApplicationStatusStore
from job_agent.config import ROOT
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.package_index_service import PackageIndexService
from job_agent.services.review_bundle_service import ReviewBundleService
from job_agent.web.formatting import markdown_to_html


def build_jobs_view(app_statuses: list[str], categories: list[str], root: Path = ROOT) -> dict:
    jobs = PackageIndexService(root).list_unique_jobs()
    if app_statuses:
        jobs = [job for job in jobs if job.get("application_status") in app_statuses]
    if categories:
        jobs = [job for job in jobs if job.get("match_category") in categories]
    return {"jobs": jobs, "filters": {"app_statuses": app_statuses, "categories": categories}}


def build_job_detail_view(job_id: str, run_id: str = "", root: Path = ROOT) -> dict:
    service = PackageIndexService(root)
    package = service.find_package(job_id, run_id)
    if not package:
        raise KeyError(job_id)
    files = service.read_package_files(package)
    status = ApplicationStatusStore(root).get(job_id)
    return {
        "package": package,
        "files": files,
        "status": status,
        "statuses": sorted(APPLICATION_STATUSES),
        "render_md": markdown_to_html,
        "cv_reference": CvReferenceService(root).get_cv_reference(),
        "review_bundle": ReviewBundleService().build(package, files, status),
    }
