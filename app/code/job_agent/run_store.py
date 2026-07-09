from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import ROOT
from .io.json_store import read_json, read_json_or_recover, write_json
from .paths import output_dir

RUN_STATUSES = {"pending", "running", "completed", "failed", "cancelled", "crashed"}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class RunOptions:
    use_llm: bool = False
    ai_enhanced_search: bool = False
    include_seen: bool = False
    include_weak: bool = False
    mark_seen: bool = False
    generate_materials: bool = False
    is_test: bool = False
    detail_extraction_limit: int | None = 25
    append_to_daily_run: bool = False
    llm_model: str = ""
    full_source_ingestion: bool = False
    include_disabled_sources: bool = False
    detail_pause_every_jobs: int = 0
    detail_pause_seconds: float = 0.0
    wait_for_source_access: bool = False
    source_access_wait_timeout_seconds: float = 0.0
    source_access_wait_poll_seconds: float = 2.0


@dataclass
class RunEvent:
    run_id: str
    event_type: str
    message: str
    phase: str = ""
    status: str = "running"
    current_source: str = ""
    current_job: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)


@dataclass
class RunRecord:
    run_id: str
    started_at: str
    finished_at: str = ""
    status: str = "pending"
    options: dict[str, Any] = field(default_factory=dict)
    total_loaded: int = 0
    new_roles: int = 0
    changed_roles: int = 0
    strong_matches: int = 0
    exploratory_matches: int = 0
    weak_matches: int = 0
    excluded_roles: int = 0
    source_warnings: int = 0
    generated_job_count: int = 0
    digest_path: str = ""
    excluded_path: str = ""
    run_log_path: str = ""
    events_path: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    total_estimated_llm_cost: float | None = None
    error_message: str = ""
    is_test: bool = False
    visibility: str = "active"
    archived_at: str = ""
    deleted_at: str = ""


class RunStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.base_dir = output_dir(root) / "runs"
        self.registry_path = self.base_dir / "runs.json"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            write_json(self.registry_path, [])

    def create_run(self, options: RunOptions) -> RunRecord:
        run_id = self.new_run_id()
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        record = RunRecord(
            run_id=run_id,
            started_at=utc_now(),
            status="pending",
            options=asdict(options),
            is_test=options.is_test,
            run_log_path=str(run_dir / "run.log"),
            events_path=str(run_dir / "events.jsonl"),
        )
        self.upsert(record)
        return record

    def upsert(self, record: RunRecord) -> None:
        records = [
            item
            for item in self.list_runs(include_archived=True, include_deleted=True, include_tests=True)
            if item.run_id != record.run_id
        ]
        records.append(record)
        records.sort(key=lambda item: item.started_at, reverse=True)
        write_json(self.registry_path, [asdict(item) for item in records])

    def update(self, run_id: str, **updates: Any) -> RunRecord:
        record = self.get(run_id, include_archived=True, include_deleted=True, include_tests=True)
        if record is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        for key, value in updates.items():
            setattr(record, key, value)
        self.upsert(record)
        return record

    def get(
        self, run_id: str, include_archived: bool = True, include_deleted: bool = True, include_tests: bool = True
    ) -> RunRecord | None:
        for record in self.list_runs(
            include_archived=include_archived, include_deleted=include_deleted, include_tests=include_tests
        ):
            if record.run_id == run_id:
                return record
        return None

    def list_runs(
        self, include_archived: bool = False, include_deleted: bool = False, include_tests: bool = True
    ) -> list[RunRecord]:
        data = read_json(self.registry_path, [], strict=True)
        records = [self._record_from_mapping(item) for item in data]
        if not include_archived:
            records = [record for record in records if record.visibility != "archived"]
        if not include_deleted:
            records = [record for record in records if record.visibility != "deleted"]
        if not include_tests:
            records = [record for record in records if not record.is_test]
        return records

    def recover_corrupt_registry(self) -> Path | None:
        _, backup = read_json_or_recover(self.registry_path, [])
        return backup

    def archive(self, run_id: str) -> RunRecord:
        record = self.update(run_id, visibility="archived", archived_at=utc_now())
        self.append_event(
            RunEvent(run_id, "run_archived", "Run archived.", phase="run_lifecycle", status=record.status)
        )
        return record

    def soft_delete(self, run_id: str) -> RunRecord:
        record = self.update(run_id, visibility="deleted", deleted_at=utc_now())
        self.append_event(
            RunEvent(run_id, "run_deleted", "Run moved to deleted runs.", phase="run_lifecycle", status=record.status)
        )
        return record

    def restore(self, run_id: str) -> RunRecord:
        record = self.update(run_id, visibility="active", archived_at="", deleted_at="")
        self.append_event(
            RunEvent(
                run_id, "run_restored", "Run restored to active runs.", phase="run_lifecycle", status=record.status
            )
        )
        return record

    def append_event(self, event: RunEvent) -> None:
        run_dir = self.run_dir(event.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        event_line = json.dumps(asdict(event), ensure_ascii=False)
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(event_line + "\n")
        with (run_dir / "run.log").open("a", encoding="utf-8") as handle:
            current = (
                f" [{event.current_source or event.current_job}]" if event.current_source or event.current_job else ""
            )
            handle.write(f"{event.timestamp} {event.status.upper()} {event.phase}{current}: {event.message}\n")

    def read_events(self, run_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        path = self.run_dir(run_id) / "events.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        if limit is not None:
            lines = lines[-limit:]
        return [json.loads(line) for line in lines if line.strip()]

    def recover_stale_runs(self) -> list[RunRecord]:
        recovered = []
        for record in self.list_runs():
            if record.status in {"pending", "running"} or record.status not in RUN_STATUSES:
                record.status = "crashed"
                record.finished_at = record.finished_at or utc_now()
                record.error_message = (
                    record.error_message
                    or "Marked crashed on app startup because the previous process exited without completing this run."
                )
                self.upsert(record)
                self.append_event(
                    RunEvent(
                        run_id=record.run_id,
                        event_type="run_recovered_as_crashed",
                        message=record.error_message,
                        phase="startup_recovery",
                        status="crashed",
                    )
                )
                recovered.append(record)
        return recovered

    def run_dir(self, run_id: str) -> Path:
        return self.base_dir / run_id

    @staticmethod
    def _record_from_mapping(item: dict[str, Any]) -> RunRecord:
        allowed = RunRecord.__dataclass_fields__.keys()
        return RunRecord(**{key: item[key] for key in allowed if key in item})

    @staticmethod
    def new_run_id() -> str:
        return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
