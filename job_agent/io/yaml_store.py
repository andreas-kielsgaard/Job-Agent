from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .atomic import atomic_write_text


def read_yaml(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return default if data is None else data


def write_yaml(path: Path, data: Any) -> None:
    atomic_write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
