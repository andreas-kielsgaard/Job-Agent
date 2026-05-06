from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import ROOT
from .models import Job


class JobStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = root / "jobs" / "seen_jobs.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def filter_new(self, jobs: list[Job]) -> list[Job]:
        seen = set(json.loads(self.path.read_text(encoding="utf-8")))
        new_jobs = [job for job in jobs if self.job_id(job) not in seen]
        return new_jobs

    def mark_seen(self, jobs: list[Job]) -> None:
        seen = set(json.loads(self.path.read_text(encoding="utf-8")))
        for job in jobs:
            seen.add(self.job_id(job))
        self.path.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")

    @staticmethod
    def job_id(job: Job) -> str:
        stable = "|".join([job.source, job.url, job.title, job.company]).lower()
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]
