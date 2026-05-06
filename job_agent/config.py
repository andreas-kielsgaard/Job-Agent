from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]


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
    profile_dir = root / "profile"
    if not profile_dir.exists():
        profile_dir = root / "profile.example"
    data: dict[str, Any] = {}
    for name in ["contact", "preferences", "skills", "experience"]:
        data.update(load_yaml(profile_dir / f"{name}.yaml"))
    data["canonical_cv"] = load_text(profile_dir / "canonical-cv.md")
    data["writing_style"] = load_text(profile_dir / "writing-style.md")
    return data
