from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .config import ROOT
from .io.json_store import read_json, write_json
from .models import Job, JobState, SeenJobRecord, normalize_text


class JobStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = root / "jobs" / "seen_jobs.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            write_json(self.path, [])

    def classify(self, jobs: list[Job], today: date | None = None) -> list[JobState]:
        today_text = str(today or date.today())
        records = self._load_records()
        by_stable = {record.stable_id: record for record in records}
        by_fuzzy = {record.fuzzy_key: record for record in records}
        states: list[JobState] = []

        for job in jobs:
            stable_id = self.job_id(job)
            fuzzy_key = self.fuzzy_key(job)
            content_hash = self.content_hash(job)
            existing = by_stable.get(stable_id) or by_fuzzy.get(fuzzy_key)
            if existing is None:
                status = "new"
                if not job.first_seen_date:
                    job.first_seen_date = today_text
            elif existing.content_hash != content_hash:
                status = "changed"
                job.first_seen_date = existing.first_seen_date
            else:
                status = "previously_seen"
                job.first_seen_date = existing.first_seen_date
            states.append(
                JobState(job=job, stable_id=stable_id, fuzzy_key=fuzzy_key, content_hash=content_hash, status=status)
            )

        return states

    def filter_new(self, jobs: list[Job]) -> list[Job]:
        return [state.job for state in self.classify(jobs) if state.status in {"new", "changed"}]

    def mark_seen(self, states_or_jobs: list[JobState] | list[Job]) -> None:
        today_text = str(date.today())
        records = self._load_records()
        by_stable = {record.stable_id: record for record in records}

        for item in states_or_jobs:
            if isinstance(item, JobState):
                state = item
            else:
                job = item
                state = JobState(job, self.job_id(job), self.fuzzy_key(job), self.content_hash(job), "new")

            existing = by_stable.get(state.stable_id)
            if existing:
                existing.last_seen_date = today_text
                existing.content_hash = state.content_hash
                existing.status = state.status
            else:
                by_stable[state.stable_id] = SeenJobRecord(
                    stable_id=state.stable_id,
                    fuzzy_key=state.fuzzy_key,
                    title=state.job.title,
                    company=state.job.company,
                    source=state.job.source,
                    url=state.job.url,
                    first_seen_date=state.job.first_seen_date or today_text,
                    last_seen_date=today_text,
                    content_hash=state.content_hash,
                    status=state.status,
                )

        write_json(self.path, [asdict(record) for record in by_stable.values()])

    @staticmethod
    def job_id(job: Job) -> str:
        stable = "|".join([job.source, job.url, job.application_url, job.title, job.company]).lower()
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def fuzzy_key(job: Job) -> str:
        description_fingerprint = " ".join(normalize_text(job.description).split()[:80])
        stable = "|".join(
            [
                job.normalized_title,
                normalize_text(job.location),
                normalize_text(job.company or job.recruiter),
                normalize_text(job.start_date),
                hashlib.sha256(description_fingerprint.encode("utf-8")).hexdigest()[:10],
            ]
        )
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def content_hash(job: Job) -> str:
        content = "|".join(
            str(part)
            for part in [job.title, job.company, job.location, job.rate, job.description, job.deadline, job.posted_date]
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def _load_records(self) -> list[SeenJobRecord]:
        data = read_json(self.path, [], strict=True)
        if data and isinstance(data[0], str):
            return [
                SeenJobRecord(
                    stable_id=item,
                    fuzzy_key=item,
                    title="Unknown",
                    company="Unknown",
                    source="Unknown",
                    url="",
                    first_seen_date="unknown",
                    last_seen_date="unknown",
                    content_hash="unknown",
                    status="previously_seen",
                )
                for item in data
            ]
        return [SeenJobRecord(**item) for item in data]
