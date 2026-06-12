from __future__ import annotations

from pathlib import Path

from job_agent.config import ROOT
from job_agent.env import load_env
from job_agent.llm import LlmService
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
    env = load_env(root)
    llm_configured = LlmService(root).is_configured()
    default_options = latest_run.options if latest_run else {
        "use_llm": env.get("CLAUDE_USE_BY_DEFAULT") == "true",
        "ai_enhanced_search": False,
        "include_seen": False,
        "include_weak": False,
        "mark_seen": False,
        "generate_materials": False,
        "detail_extraction_limit": 25,
    }
    if not llm_configured:
        default_options = {**default_options, "use_llm": False, "ai_enhanced_search": False}
    return {
        "title": "Dashboard",
        "runs": runs[:8],
        "active_run": next((run for run in runs if run.status in {"pending", "running"}), None),
        "latest_run": latest_run,
        "dashboard_stats": StatsService(root).build_dashboard_stats(runs),
        "default_options": default_options,
        "llm_configured": llm_configured,
        "env": env,
    }
