from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from job_agent.config import ROOT
from job_agent.io.json_store import read_json, write_json
from job_agent.paths import profile_dir


class CvProfileDraftService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        draft_dir = profile_dir(root) / "drafts"
        self.path = draft_dir / "cv-profile-draft.json"
        self.task_path = draft_dir / "cv-profile-task.json"
        self.applied_path = draft_dir / "cv-profile-applied.json"

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

    def applied_sections(self) -> dict[str, dict[str, Any]]:
        data = read_json(self.applied_path, {}, strict=False)
        sections = data.get("sections", {}) if isinstance(data, dict) else {}
        if not isinstance(sections, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for target, record in sections.items():
            if not isinstance(record, dict):
                continue
            key = str(target or "").strip()
            if not key:
                continue
            normalized[key] = {
                "target": key,
                "label": str(record.get("label") or _target_label(key)),
                "source_label": str(record.get("source_label") or "CV"),
                "applied_at": str(record.get("applied_at") or ""),
            }
        return normalized

    def record_applied_sections(self, targets: list[str], *, source_label: str = "CV") -> dict[str, dict[str, Any]]:
        cleaned = [str(target or "").strip() for target in targets if str(target or "").strip()]
        if not cleaned:
            return self.applied_sections()
        now = utc_now()
        sections = self.applied_sections()
        for target in cleaned:
            sections[target] = {
                "target": target,
                "label": _target_label(target),
                "source_label": source_label or "CV",
                "applied_at": now,
            }
        self.applied_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.applied_path, {"sections": sections, "updated_at": now})
        return sections

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
            "section_details": _section_details(data, targets),
            "json": json.dumps(data, ensure_ascii=False),
            "url": "/setup#cv-profile-draft",
        }
        return normalized


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _target_label(target: str) -> str:
    labels = {
        "contact": "Profile basics",
        "canonical_cv": "CV narrative",
        "skills": "Skill matrix",
        "experience": "Case studies",
        "preferences": "Availability and constraints",
        "match_engine": "Matchmaking settings",
    }
    return labels.get(target, target.replace("_", " ").title())


def _section_details(data: dict[str, Any], targets: list[str]) -> dict[str, str]:
    details: dict[str, str] = {}
    for target in targets:
        value: Any
        if target == "contact":
            contact_yaml = data.get("contact_yaml") if isinstance(data.get("contact_yaml"), dict) else {}
            value = contact_yaml.get("contact") if isinstance(contact_yaml.get("contact"), dict) else contact_yaml
        elif target == "canonical_cv":
            value = str(data.get("canonical_cv") or "")
        elif target == "skills":
            value = data.get("skills_yaml") if isinstance(data.get("skills_yaml"), dict) else {}
        elif target == "experience":
            value = data.get("experience_yaml") if isinstance(data.get("experience_yaml"), dict) else {}
        elif target == "preferences":
            value = data.get("preferences_yaml") if isinstance(data.get("preferences_yaml"), dict) else {}
        elif target == "match_engine":
            value = data.get("match_engine") if isinstance(data.get("match_engine"), dict) else {}
        else:
            value = data.get(target)
        if isinstance(value, str):
            details[target] = value.strip()
        else:
            details[target] = json.dumps(value, ensure_ascii=False, indent=2)
    return details
