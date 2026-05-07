from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text


def read_json(path: Path, default: Any, *, strict: bool = False) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError(f"Invalid JSON in {path}") from exc
        return default


def write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def backup_corrupt_file(path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(path.suffix + f".corrupt-{timestamp}")
    counter = 1
    while backup.exists():
        backup = path.with_suffix(path.suffix + f".corrupt-{timestamp}-{counter}")
        counter += 1
    path.replace(backup)
    return backup


def read_json_or_recover(path: Path, default: Any) -> tuple[Any, Path | None]:
    try:
        return read_json(path, default, strict=True), None
    except ValueError:
        backup = backup_corrupt_file(path)
        write_json(path, default)
        return default, backup
