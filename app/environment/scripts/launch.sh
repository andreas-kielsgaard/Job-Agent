#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CODE_DIR="$ROOT/app/code"
VENV_DIR="$ROOT/app/environment/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
REQUIREMENTS="$ROOT/app/environment/requirements.txt"
PLAYWRIGHT_REQUIREMENTS="$ROOT/app/environment/requirements-playwright.txt"
DEPENDENCY_STAMP="$VENV_DIR/.job-agent-dependencies.json"
DEPENDENCY_STAMP_SCRIPT="$ROOT/app/environment/scripts/dependency_stamp.py"
URL="http://127.0.0.1:8765/"
HEALTH_URL="http://127.0.0.1:8765/api/health"

python_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

find_python() {
  for candidate in python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

install_python_with_consent() {
  printf 'Python 3.11+ was not found. Install Python with Homebrew now? [y/N] '
  read -r choice
  case "$choice" in
    y|Y|yes|YES)
      if ! command -v brew >/dev/null 2>&1; then
        echo "Homebrew is not available. Install Python 3.11+ from https://www.python.org/downloads/ and run this again."
        exit 1
      fi
      brew install python@3.11
      ;;
    *)
      echo "Python 3.11+ is required. Install it, then run this launcher again."
      exit 1
      ;;
  esac
}

health_ok() {
  curl -fsS "$HEALTH_URL" >/dev/null 2>&1
}

health_json() {
  curl -fsS "$HEALTH_URL" 2>/dev/null || true
}

health_field() {
  "$VENV_PYTHON" -c "import json, sys; data=json.load(sys.stdin); print(data.get('$1') or '')" 2>/dev/null || true
}

current_app_version() {
  PYTHONPATH="$CODE_DIR${PYTHONPATH:+:$PYTHONPATH}" "$VENV_PYTHON" - <<PY
from pathlib import Path
from job_agent.web.runtime import compute_app_version
print(compute_app_version(Path("$ROOT")))
PY
}

same_checkout_root() {
  local running_root
  running_root="$1"
  if [[ -z "$running_root" ]]; then
    return 1
  fi
  if [[ -d "$running_root" ]]; then
    running_root="$(cd "$running_root" && pwd)"
  fi
  [[ "$running_root" == "$ROOT" ]]
}

job_agent_pids() {
  pgrep -f '[p]ython.*-m job_agent\.web\.app' 2>/dev/null || true
}

listener_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:8765 -sTCP:LISTEN 2>/dev/null || true
  fi
}

stop_job_agent_web() {
  for pid in $(job_agent_pids); do
    kill "$pid" 2>/dev/null || true
  done
}

remove_extra_job_agent_web_processes() {
  local listeners pids keep parent has_match
  listeners=" $(listener_pids | tr '\n' ' ') "
  if [[ -z "${listeners// }" ]]; then
    return
  fi

  pids="$(job_agent_pids | tr '\n' ' ')"
  has_match=0
  for pid in $pids; do
    case "$listeners" in
      *" $pid "*) has_match=1 ;;
    esac
  done
  if [[ "$has_match" -eq 0 ]]; then
    return
  fi

  keep=""
  for pid in $pids; do
    case "$listeners" in
      *" $pid "*)
        keep="$keep $pid"
        while true; do
          parent="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
          case " $pids " in
            *" $parent "*)
              keep="$keep $parent"
              pid="$parent"
              ;;
            *) break ;;
          esac
        done
        ;;
    esac
  done

  for pid in $pids; do
    case " $keep " in
      *" $pid "*) ;;
      *) kill "$pid" 2>/dev/null || true ;;
    esac
  done
}

dependencies_verified() {
  [[ -f "$DEPENDENCY_STAMP" ]] || return 1
  "$VENV_PYTHON" "$DEPENDENCY_STAMP_SCRIPT" check \
    --stamp "$DEPENDENCY_STAMP" \
    --requirements "$REQUIREMENTS" \
    --requirements "$PLAYWRIGHT_REQUIREMENTS" >/dev/null 2>&1 &&
    playwright_chromium_ready
}

mark_dependencies_verified() {
  "$VENV_PYTHON" "$DEPENDENCY_STAMP_SCRIPT" mark \
    --stamp "$DEPENDENCY_STAMP" \
    --requirements "$REQUIREMENTS" \
    --requirements "$PLAYWRIGHT_REQUIREMENTS" >/dev/null
}

playwright_chromium_ready() {
  "$VENV_PYTHON" - <<'PY' >/dev/null 2>&1
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    browser.close()
PY
}

PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  install_python_with_consent
  PYTHON_BIN="$(find_python || true)"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3.11+ is still not available on PATH."
  exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! dependencies_verified; then
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS"
  "$VENV_PYTHON" -m pip install -r "$PLAYWRIGHT_REQUIREMENTS"
  "$VENV_PYTHON" -m playwright install chromium
  mark_dependencies_verified
fi

export PYTHONPATH="$CODE_DIR${PYTHONPATH:+:$PYTHONPATH}"
"$VENV_PYTHON" -m job_agent.bootstrap --root "$ROOT"

CURRENT_VERSION="$(current_app_version)"
HEALTH_JSON="$(health_json)"
if [[ -n "$HEALTH_JSON" ]]; then
  RUNNING_VERSION="$(printf '%s' "$HEALTH_JSON" | health_field app_version)"
  RUNNING_ROOT="$(printf '%s' "$HEALTH_JSON" | health_field root)"
  ACTIVE_RUN_ID="$(printf '%s' "$HEALTH_JSON" | health_field active_run_id)"
  if [[ "$RUNNING_VERSION" != "$CURRENT_VERSION" || ! same_checkout_root "$RUNNING_ROOT" ]]; then
    if [[ -n "$ACTIVE_RUN_ID" ]]; then
      printf 'A different Job Agent server is running an active run (%s). Stop it and launch this checkout? [y/N] ' "$ACTIVE_RUN_ID"
      read -r choice
      case "$choice" in
        y|Y|yes|YES) ;;
        *) open "$URL"; exit 0 ;;
      esac
    fi
    stop_job_agent_web
    HEALTH_JSON=""
  else
    remove_extra_job_agent_web_processes
    HEALTH_JSON="$(health_json)"
  fi
fi

if [[ -z "$HEALTH_JSON" ]]; then
  mkdir -p "$ROOT/runtime"
  JOB_AGENT_IDLE_SHUTDOWN_SECONDS=120 "$VENV_PYTHON" -m job_agent.web.app > "$ROOT/runtime/web.log" 2>&1 &
  for _ in $(seq 1 40); do
    sleep 0.5
    if health_ok; then
      break
    fi
  done
fi

if ! health_ok; then
  echo "Job Agent web app did not become ready at $URL"
  exit 1
fi

open "$URL"
