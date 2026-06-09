from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from job_agent.config import ROOT
from job_agent.io.json_store import read_json, write_json


class CvProfileDraftService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.path = root / "profile" / "drafts" / "cv-profile-draft.json"
        self.task_path = root / "profile" / "drafts" / "cv-profile-task.json"

    def active_draft(self) -> dict[str, Any]:
        draft = read_json(self.path, {}, strict=False)
        if not isinstance(draft, dict) or draft.get("status") != "unreviewed":
            return {}
        return self._normalize(draft)

    def save_draft(self, draft: dict[str, Any], *, source_label: str = "CV", task_id: str = "") -> dict[str, Any]:
        now = utc_now()
        data = {
            "id": f"cvdraft-{uuid4().hex[:10]}",
            "status": "unreviewed",
            "title": "CV profile draft ready",
            "source_label": source_label,
            "task_id": task_id,
            "created_at": now,
            "updated_at": now,
            "targets": list(draft.get("targets") or []),
            "sections": list(draft.get("sections") or []),
            "data": draft.get("data") if isinstance(draft.get("data"), dict) else {},
        }
        write_json(self.path, data)
        return self._normalize(data)

    def clear_active_draft(self, draft_id: str = "") -> bool:
        draft = self.active_draft()
        if not draft:
            return False
        if draft_id and draft.get("id") != draft_id:
            return False
        self.path.unlink(missing_ok=True)
        return True

    def has_active_draft(self, draft_id: str = "") -> bool:
        draft = self.active_draft()
        if not draft:
            return False
        return not draft_id or draft.get("id") == draft_id

    def active_work_item(self) -> dict[str, Any]:
        draft = self.active_draft()
        if not draft:
            return {}
        return {
            "kind": "profile",
            "task_id": str(draft.get("task_id") or f"profile-draft-{draft['id']}"),
            "draft_id": draft["id"],
            "href": "/setup#cv-profile-draft",
            "title": "CV profile draft ready",
            "status": "completed",
            "stage": "Ready for review",
            "message": "A CV profile draft is waiting for you to apply or discard it.",
            "progress_percent": 100,
            "source_name": "Profile",
            "started_at": str(draft.get("created_at") or ""),
            "finished_at": str(draft.get("updated_at") or ""),
            "error_message": "",
        }

    def save_task(self, task: dict[str, Any]) -> None:
        if not task.get("task_id"):
            return
        write_json(self.task_path, {**task, "updated_at": utc_now()})

    def active_task(self) -> dict[str, Any]:
        task = read_json(self.task_path, {}, strict=False)
        if not isinstance(task, dict) or not task.get("task_id"):
            return {}
        status = str(task.get("status") or "").lower()
        draft_id = str(task.get("draft_id") or "")
        if status == "completed" and draft_id and self.has_active_draft(draft_id):
            return self._task_payload(task)
        if status in {"pending", "running"} and not self._stale_task(task):
            return self._task_payload(task)
        self.clear_task(str(task.get("task_id") or ""))
        return {}

    def clear_task(self, task_id: str = "") -> bool:
        task = read_json(self.task_path, {}, strict=False)
        if not isinstance(task, dict) or not task:
            return False
        if task_id and task.get("task_id") != task_id:
            return False
        self.task_path.unlink(missing_ok=True)
        return True

    @staticmethod
    def _stale_task(task: dict[str, Any]) -> bool:
        stamp = str(task.get("updated_at") or task.get("started_at") or "")
        try:
            updated = datetime.fromisoformat(stamp)
        except ValueError:
            return False
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return (datetime.now(UTC) - updated).total_seconds() > 1800

    @staticmethod
    def _task_payload(task: dict[str, Any]) -> dict[str, Any]:
        return {
            **task,
            "kind": "profile",
            "source_name": "Profile",
        }

    def _normalize(self, draft: dict[str, Any]) -> dict[str, Any]:
        data = draft.get("data") if isinstance(draft.get("data"), dict) else {}
        sections = draft.get("sections") if isinstance(draft.get("sections"), list) else []
        targets = draft.get("targets") if isinstance(draft.get("targets"), list) else []
        normalized = {
            **draft,
            "data": data,
            "sections": sections,
            "targets": targets,
            "json": json.dumps(data, ensure_ascii=False),
            "url": "/setup#cv-profile-draft",
        }
        return normalized


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
