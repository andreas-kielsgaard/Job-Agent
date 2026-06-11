from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from job_agent.paths import find_repo_root, profile_input_dir

ROOT = find_repo_root(Path(__file__))


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_profile(root: Path = ROOT) -> dict[str, Any]:
    profile_dir = profile_input_dir(root)
    data: dict[str, Any] = {}
    for name in ["contact", "preferences", "skills", "experience"]:
        data.update(load_yaml(profile_dir / f"{name}.yaml"))
    application_examples = load_yaml(profile_dir / "application-examples.yaml")
    if isinstance(application_examples, dict):
        data["application_examples"] = application_examples.get("application_examples", [])
    elif isinstance(application_examples, list):
        data["application_examples"] = application_examples
    else:
        data["application_examples"] = []
    data["canonical_cv"] = load_text(profile_dir / "canonical-cv.md")
    data["writing_style"] = load_text(profile_dir / "writing-style.md")
    return data
