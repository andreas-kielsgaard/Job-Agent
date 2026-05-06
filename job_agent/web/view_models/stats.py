from __future__ import annotations

from pathlib import Path

from job_agent.config import ROOT
from job_agent.services.stats_service import StatsService


def build_stats_view(root: Path = ROOT) -> dict:
    return StatsService(root).build_stats_page()
