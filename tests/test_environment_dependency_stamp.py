from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_dependency_stamp_module():
    module_path = Path(__file__).resolve().parents[1] / "app" / "environment" / "scripts" / "dependency_stamp.py"
    spec = importlib.util.spec_from_file_location("dependency_stamp", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dependency_stamp_tracks_requirements_changes(tmp_path: Path) -> None:
    dependency_stamp = _load_dependency_stamp_module()
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi==1.0\n", encoding="utf-8")
    stamp = tmp_path / ".venv" / ".job-agent-dependencies.json"

    assert not dependency_stamp.stamp_is_current(stamp, [requirements])

    dependency_stamp.write_stamp(stamp, [requirements])
    assert dependency_stamp.stamp_is_current(stamp, [requirements])

    requirements.write_text("fastapi==1.1\n", encoding="utf-8")
    assert not dependency_stamp.stamp_is_current(stamp, [requirements])
