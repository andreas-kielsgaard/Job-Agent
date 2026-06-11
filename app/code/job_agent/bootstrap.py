from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from job_agent.config import ROOT
from job_agent.paths import (
    cv_upload_dir,
    env_defaults_file,
    env_file,
    output_dir,
    profile_defaults_dir,
    profile_dir,
    runtime_jobs_dir,
    source_defaults_dir,
    sources_dir,
    uploads_dir,
)


@dataclass
class BootstrapResult:
    root: Path
    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)


def bootstrap_project(root: Path = ROOT) -> BootstrapResult:
    root = Path(root)
    result = BootstrapResult(root=root)

    _ensure_dir(profile_dir(root).parent, result)
    _ensure_dir(uploads_dir(root), result)
    _ensure_dir(cv_upload_dir(root), result)
    _ensure_dir(output_dir(root), result)
    _ensure_dir(runtime_jobs_dir(root), result)

    _copy_tree_if_missing(profile_defaults_dir(root), profile_dir(root), result)
    _copy_tree_if_missing(source_defaults_dir(root), sources_dir(root), result)
    _copy_file_if_missing(env_defaults_file(root), env_file(root), result)

    return result


def _ensure_dir(path: Path, result: BootstrapResult) -> None:
    if path.exists():
        result.existing.append(_display(path, result.root))
        return
    path.mkdir(parents=True, exist_ok=True)
    result.created.append(_display(path, result.root))


def _copy_tree_if_missing(source: Path, target: Path, result: BootstrapResult) -> None:
    if target.exists():
        result.existing.append(_display(target, result.root))
        return
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    result.created.append(_display(target, result.root))


def _copy_file_if_missing(source: Path, target: Path, result: BootstrapResult) -> None:
    if target.exists():
        result.existing.append(_display(target, result.root))
        return
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    result.created.append(_display(target, result.root))


def _display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply first-run Job Agent defaults.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    result = bootstrap_project(args.root)
    if result.created:
        print("Created:")
        for item in result.created:
            print(f"  - {item}")
    else:
        print("No first-run files needed to be created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
