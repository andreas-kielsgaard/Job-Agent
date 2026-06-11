from __future__ import annotations

from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.services.setup_service import SetupService


def build_match_sandbox_view(
    root: Path = ROOT,
    *,
    job_id: str = "",
    run_id: str = "",
    form: Any | None = None,
    settings_saved: bool = False,
) -> dict[str, Any]:
    service = SetupService(root)
    if form is not None:
        settings = service.match_engine_settings_from_form(form)
        sandbox_input = service.sandbox_input_from_form(form)
    else:
        settings = service.load_match_engine_settings()
        sandbox_input = service.sandbox_input_from_package(job_id, run_id) if job_id else None
        sandbox_input = sandbox_input or service.default_sandbox_input()
    return {
        "title": "Scoring Sandbox",
        "match_engine": service.match_engine_form_model(settings),
        "sandbox_input": sandbox_input,
        "match_preview": service.score_sandbox_input(sandbox_input, settings),
        "settings_saved": settings_saved,
        "source_job_id": job_id,
        "source_run_id": run_id,
    }
