from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from job_agent.config import ROOT
from job_agent.run_service import run_daily_agent
from job_agent.run_store import RunOptions, RunRecord, RunStore


class WebRuntime:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.last_request_at = time.time()
        self.idle_monitor_started = False
        self.app_version = ""

    def mark_activity(self) -> None:
        self.last_request_at = time.time()

    def startup(self) -> None:
        self.app_version = compute_app_version(self.root)
        RunStore(self.root).recover_stale_runs()
        if self.idle_monitor_started:
            return
        self.idle_monitor_started = True
        seconds = int(os.getenv("JOB_AGENT_IDLE_SHUTDOWN_SECONDS", "0") or "0")
        if seconds <= 0:
            return
        thread = threading.Thread(target=self._idle_shutdown_loop, args=(seconds,), daemon=True)
        thread.start()

    def health_payload(self) -> dict[str, str]:
        active_run = self.active_run()
        return {
            "status": "ok",
            "time": datetime.now(timezone.utc).isoformat(),
            "app_version": self.app_version or compute_app_version(self.root),
            "active_run_id": active_run.run_id if active_run else "",
            "active_run_status": active_run.status if active_run else "",
        }

    def launch_daily_run(self, options: RunOptions) -> RunRecord:
        store = RunStore(self.root)
        record = store.create_run(options)
        self.executor.submit(run_daily_agent, options, None, self.root, record.run_id)
        return record

    def active_run(self) -> RunRecord | None:
        return next((run for run in RunStore(self.root).list_runs() if run.status in {"pending", "running"}), None)

    def has_active_run(self) -> bool:
        try:
            return self.active_run() is not None
        except Exception:
            return True

    def _idle_shutdown_loop(self, seconds: int) -> None:
        while True:
            time.sleep(10)
            if time.time() - self.last_request_at < seconds:
                continue
            if self.has_active_run():
                continue
            os._exit(0)


def compute_app_version(root: Path) -> str:
    hasher = hashlib.sha256()
    patterns = [
        "job_agent/**/*.py",
        "job_agent/web/templates/**/*.html",
        "job_agent/web/static/**/*",
        "templates/**/*.j2",
        "prompts/**/*.md",
        "requirements.txt",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in root.glob(pattern) if path.is_file())
    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        hasher.update(relative.encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:16]


runtime = WebRuntime(ROOT)
