from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _norm_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_stamp(requirements: list[Path]) -> dict[str, Any]:
    entries = [
        {
            "path": _norm_path(path),
            "sha256": _file_digest(path),
        }
        for path in requirements
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "python_executable": _norm_path(Path(sys.executable)),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "requirements": entries,
        "verified_at": datetime.now(UTC).isoformat(),
    }


def stamp_is_current(stamp_path: Path, requirements: list[Path]) -> bool:
    if not stamp_path.exists():
        return False
    if not all(path.exists() for path in requirements):
        return False
    try:
        recorded = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    current = build_stamp(requirements)
    comparable_keys = ("schema_version", "python_executable", "python_version", "requirements")
    return all(recorded.get(key) == current.get(key) for key in comparable_keys)


def write_stamp(stamp_path: Path, requirements: list[Path]) -> None:
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(json.dumps(build_stamp(requirements), indent=2, sort_keys=True), encoding="utf-8")


def _requirements(values: list[str]) -> list[Path]:
    paths = [Path(value).resolve() for value in values]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise SystemExit(f"Requirements file not found: {', '.join(missing)}")
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cache successful Job Agent dependency installation checks.")
    parser.add_argument("command", choices=("check", "mark"))
    parser.add_argument("--stamp", required=True, type=Path)
    parser.add_argument("--requirements", action="append", required=True)
    args = parser.parse_args(argv)

    requirements = _requirements(args.requirements)
    stamp_path = args.stamp.resolve()
    if args.command == "check":
        return 0 if stamp_is_current(stamp_path, requirements) else 1
    write_stamp(stamp_path, requirements)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
