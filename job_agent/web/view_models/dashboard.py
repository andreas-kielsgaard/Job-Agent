from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from job_agent.config import ROOT
from job_agent.run_store import RunStore
from job_agent.services.stats_service import StatsService


def build_dashboard_view(root: Path = ROOT) -> dict:
    store = RunStore(root)
    try:
        runs = store.list_runs(include_tests=False)
    except ValueError:
        store.recover_corrupt_registry()
        runs = []
    latest_run = runs[0] if runs else None
    return {
        "runs": runs[:8],
        "active_run": next((run for run in runs if run.status in {"pending", "running"}), None),
        "latest_run": latest_run,
        "dashboard_stats": StatsService(root).build_dashboard_stats(runs),
        "default_options": latest_run.options
        if latest_run
        else {
            "use_llm": True,
            "ai_enhanced_search": False,
            "include_seen": False,
            "include_weak": False,
            "mark_seen": True,
            "generate_materials": False,
            "detail_extraction_limit": 25,
        },
        "env": dotenv_values(root / ".env"),
    }
