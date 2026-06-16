from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROGRESS_TEXT = ROOT / ".pytest-progress" / "latest.txt"


def main(argv: list[str] | None = None) -> int:
    args, pytest_args = _parse_args(argv)
    if args.show_progress:
        return _print_progress()

    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]

    command = [sys.executable, "-m", "pytest"]
    if args.coverage:
        command.extend(["--cov=job_agent", "--cov-report=term-missing"])
    command.extend(pytest_args)

    print(f"[test-handler] Running: {' '.join(command)}", flush=True)
    print(f"[test-handler] Progress file: {_relative(PROGRESS_TEXT)}", flush=True)

    try:
        result = subprocess.run(
            command,
            check=False,
            cwd=ROOT,
            env=_test_env(),
            timeout=args.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        print(f"[test-handler] Timed out after {args.timeout_seconds:.0f}s.", flush=True)
        _print_progress()
        return 124

    if result.returncode != 0:
        print(f"[test-handler] Pytest exited with status {result.returncode}.", flush=True)
        _print_progress()
    else:
        print(f"[test-handler] Pytest passed. Latest progress: {_relative(PROGRESS_TEXT)}", flush=True)
    return result.returncode


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Run pytest through the Job Agent test handler with timeout recovery breadcrumbs."
    )
    parser.add_argument("--coverage", action="store_true", help="Run pytest with the project coverage report.")
    parser.add_argument("--show-progress", action="store_true", help="Print the latest pytest progress file and exit.")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Optional handler-level timeout. On timeout, the latest pytest progress is printed.",
    )
    return parser.parse_known_args(argv)


def _test_env() -> dict[str, str]:
    env = os.environ.copy()
    app_code = str(ROOT / "app" / "code")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = app_code if not existing_pythonpath else os.pathsep.join([app_code, existing_pythonpath])
    return env


def _print_progress() -> int:
    if not PROGRESS_TEXT.exists():
        print(f"[test-handler] No pytest progress file found at {_relative(PROGRESS_TEXT)}.", flush=True)
        return 1
    print(f"[test-handler] Latest pytest progress from {_relative(PROGRESS_TEXT)}:", flush=True)
    print(PROGRESS_TEXT.read_text(encoding="utf-8"), end="", flush=True)
    return 0


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
