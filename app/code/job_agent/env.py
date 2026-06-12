from __future__ import annotations

from io import StringIO
from pathlib import Path

from dotenv import dotenv_values

from job_agent.config import ROOT
from job_agent.paths import env_file


def load_env(root: Path = ROOT) -> dict[str, str]:
    path = env_file(root)
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    values = dotenv_values(stream=StringIO(text))
    return {str(key): str(value) for key, value in values.items() if key and value is not None}
