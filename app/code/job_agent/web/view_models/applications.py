from __future__ import annotations

from pathlib import Path
from typing import Any

from job_agent.application_models import APPLICATION_OUTCOMES
from job_agent.config import ROOT
from job_agent.services.application_index_service import ApplicationIndexService
from job_agent.web.formatting import markdown_to_html

COMMUNICATION_FILTERS = ["linked", "manual events", "no thread linked"]


def build_applications_view(filters: dict[str, Any] | None = None, root: Path = ROOT) -> dict[str, Any]:
    filters = filters or {}
    applications = ApplicationIndexService(root).list_rows(filters)
    return {
        "title": "Applications",
        "applications": applications,
        "result_count": len(applications),
        "filters": {
            "outcome": str(filters.get("outcome") or ""),
            "communication": str(filters.get("communication") or ""),
            "q": str(filters.get("q") or ""),
        },
        "outcome_options": sorted(APPLICATION_OUTCOMES),
        "communication_options": COMMUNICATION_FILTERS,
    }


def build_application_detail_view(application_id: str, root: Path = ROOT) -> dict[str, Any]:
    context = ApplicationIndexService(root).detail(application_id)
    context["render_md"] = markdown_to_html
    return context
