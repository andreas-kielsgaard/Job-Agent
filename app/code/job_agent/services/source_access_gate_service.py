from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.paths import resolve_project_path
from job_agent.services.recipes.mapping import load_project_job_board_recipe
from job_agent.services.source_registry_service import SourceRegistryService
from job_agent.services.source_session_service import SourceSessionService, SourceSessionStatus

SOURCE_ACCESS_PURPOSES = {"source_test", "listing_index", "detail_ingest", "daily_run", "full_ingest", "learn"}
SOURCE_ACCESS_STATUSES = {"ready", "not_required", "needs_login", "needs_verification", "blocked"}
_VERIFIED_EXECUTION_PURPOSES = {"listing_index", "detail_ingest", "daily_run", "full_ingest"}


@dataclass
class SourceAccessDecision:
    can_execute: bool
    status: str
    message: str
    blockers: list[str] = field(default_factory=list)
    session_required: bool = False
    session_usable: bool = False
    session_verified: bool = False
    session_scope: str = ""
    session_state_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def uses_session(self) -> bool:
        return bool(self.session_usable and self.session_state_path)


class SourceAccessGateService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.sessions = SourceSessionService(self.root)
        self.registry = SourceRegistryService(self.root)

    def evaluate(
        self,
        source_id: str,
        *,
        purpose: str,
        source: Any | None = None,
    ) -> SourceAccessDecision:
        source_id = source_id.strip()
        resolved_source = source if source is not None else self._source_for_id(source_id)
        if resolved_source is None:
            message = f"Source not found: {source_id or 'unknown source'}."
            return SourceAccessDecision(
                can_execute=False,
                status="blocked",
                message=message,
                blockers=[message],
                metadata={
                    "source_access_status": "blocked",
                    "source_access_requires_session": False,
                    "source_access_session_used": False,
                    "source_access_session_scope": "",
                    "source_access_session_status": "",
                    "source_access_session_label": "",
                    "source_access_session_verified": False,
                    "source_id": source_id,
                },
            )
        return self.evaluate_source(resolved_source, purpose=purpose, source_id=source_id)

    def evaluate_source(
        self,
        source: Any,
        *,
        purpose: str,
        source_id: str = "",
    ) -> SourceAccessDecision:
        purpose = _normalize_purpose(purpose)
        source_id = source_id or _source_id(source)
        recipe_path = _source_recipe_path(source)
        source_name = _source_name(source, source_id)
        source_url = _source_url(source)
        base_metadata = {
            "source_access_status": "not_required",
            "source_access_requires_session": False,
            "source_access_session_used": False,
            "source_access_session_scope": "",
            "source_access_session_status": "",
            "source_access_session_label": "",
            "source_access_session_verified": False,
        }
        if not recipe_path:
            if purpose == "learn":
                return self._learn_optional_session_decision(source_id, source_name, base_metadata)
            return SourceAccessDecision(
                can_execute=True,
                status="not_required",
                message="Source access session is not required.",
                metadata=base_metadata,
            )
        try:
            recipe = load_project_job_board_recipe(self.root, recipe_path)
        except (OSError, ValueError) as exc:
            if purpose == "learn":
                return self._learn_optional_session_decision(source_id, source_name, base_metadata)
            message = f"Source access could not load reading plan before execution: {exc}"
            return SourceAccessDecision(
                can_execute=False,
                status="blocked",
                message=message,
                blockers=[message],
                metadata={**base_metadata, "source_access_status": "blocked"},
            )

        session_required = bool(recipe.access.requires_session)
        session_scope = recipe.access.session_scope
        session_status = self.sessions.status_for_source(source_id, session_scope=session_scope)
        session_usable = bool(session_status.usable)
        session_verified = bool(session_status.verified_at)
        session_state_path = session_status.storage_state_path if session_usable else ""
        metadata = {
            **base_metadata,
            "source_access_status": "ready" if session_required else "not_required",
            "source_access_requires_session": session_required,
            "source_access_session_used": session_usable,
            "source_access_session_scope": session_status.session_scope,
            "source_access_setup_hint": recipe.access.setup_hint,
            "source_access_session_status": session_status.status,
            "source_access_session_label": session_status.label,
            "source_access_session_verified": session_verified,
        }

        if session_required and not session_usable:
            message = _needs_login_message(recipe.source_name or source_name, session_status)
            return SourceAccessDecision(
                can_execute=False,
                status="needs_login",
                message=message,
                blockers=[message],
                session_required=True,
                session_usable=False,
                session_verified=False,
                session_scope=session_status.session_scope,
                metadata={**metadata, "source_access_status": "needs_login"},
            )

        if session_required and purpose in _VERIFIED_EXECUTION_PURPOSES and not session_verified:
            message = (
                "Source session is connected but not verified; run the safe source test before this source is used."
            )
            return SourceAccessDecision(
                can_execute=False,
                status="needs_verification",
                message=message,
                blockers=[message],
                session_required=True,
                session_usable=True,
                session_verified=False,
                session_scope=session_status.session_scope,
                session_state_path=session_state_path,
                metadata={**metadata, "source_access_status": "needs_verification"},
            )

        if purpose in _VERIFIED_EXECUTION_PURPOSES:
            readiness_decision = self._readiness_decision(
                source_id,
                source_name=source_name,
                source_url=source_url,
                session_required=session_required,
                session_status=session_status,
                session_verified=session_verified,
                session_state_path=session_state_path,
                metadata=metadata,
            )
            if readiness_decision:
                return readiness_decision

        if session_usable:
            message = f"Using connected source session for {session_status.session_scope or source_name}."
            return SourceAccessDecision(
                can_execute=True,
                status="ready" if session_required else "not_required",
                message=message,
                session_required=session_required,
                session_usable=True,
                session_verified=session_verified,
                session_scope=session_status.session_scope,
                session_state_path=session_state_path,
                metadata=metadata,
            )
        return SourceAccessDecision(
            can_execute=True,
            status="not_required",
            message="Source access session is not required.",
            session_required=False,
            session_scope=session_status.session_scope,
            metadata=metadata,
        )

    def project_source(
        self,
        source: Any,
        *,
        purpose: str,
        source_id: str = "",
        readiness: Any | None = None,
    ) -> SourceAccessDecision:
        """Project source access from saved readiness/session state without re-running readiness checks."""
        purpose = _normalize_purpose(purpose)
        source_id = source_id or _source_id(source)
        recipe_path = _source_recipe_path(source)
        source_name = _source_name(source, source_id)
        base_metadata = {
            "source_access_status": "not_required",
            "source_access_requires_session": False,
            "source_access_session_used": False,
            "source_access_session_scope": "",
            "source_access_session_status": "",
            "source_access_session_label": "",
            "source_access_session_verified": False,
        }
        if not recipe_path:
            return SourceAccessDecision(
                can_execute=True,
                status="not_required",
                message="Source access session is not required.",
                metadata=base_metadata,
            )

        session_required, session_scope, setup_hint = _saved_session_requirements(readiness)
        if not session_required and not getattr(readiness, "last_checked_at", ""):
            try:
                recipe = load_project_job_board_recipe(self.root, recipe_path)
                session_required = bool(recipe.access.requires_session)
                session_scope = recipe.access.session_scope
                setup_hint = recipe.access.setup_hint
            except (OSError, ValueError):
                session_required = False
        session_status = self.sessions.status_for_source(source_id, session_scope=session_scope)
        session_usable = bool(session_status.usable)
        session_verified = bool(session_status.verified_at)
        session_state_path = session_status.storage_state_path if session_usable else ""
        metadata = {
            **base_metadata,
            "source_access_status": "ready" if session_required else "not_required",
            "source_access_requires_session": session_required,
            "source_access_session_used": session_usable,
            "source_access_session_scope": session_status.session_scope,
            "source_access_setup_hint": setup_hint,
            "source_access_session_status": session_status.status,
            "source_access_session_label": session_status.label,
            "source_access_session_verified": session_verified,
        }

        if session_required and not session_usable:
            message = _needs_login_message(source_name, session_status)
            return SourceAccessDecision(
                can_execute=False,
                status="needs_login",
                message=message,
                blockers=[message],
                session_required=True,
                session_usable=False,
                session_verified=False,
                session_scope=session_status.session_scope,
                metadata={**metadata, "source_access_status": "needs_login"},
            )
        if session_required and purpose in _VERIFIED_EXECUTION_PURPOSES and not session_verified:
            message = (
                "Source session is connected but not verified; run the safe source test before this source is used."
            )
            return SourceAccessDecision(
                can_execute=False,
                status="needs_verification",
                message=message,
                blockers=[message],
                session_required=True,
                session_usable=True,
                session_verified=False,
                session_scope=session_status.session_scope,
                session_state_path=session_state_path,
                metadata={**metadata, "source_access_status": "needs_verification"},
            )
        readiness_decision = _project_readiness_decision(
            readiness,
            session_required=session_required,
            session_status=session_status,
            session_verified=session_verified,
            session_state_path=session_state_path,
            metadata=metadata,
        )
        if purpose in _VERIFIED_EXECUTION_PURPOSES and readiness_decision:
            return readiness_decision
        if session_usable:
            return SourceAccessDecision(
                can_execute=True,
                status="ready" if session_required else "not_required",
                message=f"Using connected source session for {session_status.session_scope or source_name}.",
                session_required=session_required,
                session_usable=True,
                session_verified=session_verified,
                session_scope=session_status.session_scope,
                session_state_path=session_state_path,
                metadata=metadata,
            )
        return SourceAccessDecision(
            can_execute=True,
            status="not_required",
            message="Source access session is not required.",
            session_required=False,
            session_scope=session_status.session_scope,
            metadata=metadata,
        )

    def resolve_session_state_path(self, decision: SourceAccessDecision) -> Path | None:
        if not decision.session_state_path:
            return None
        return resolve_project_path(self.root, decision.session_state_path)

    def _source_for_id(self, source_id: str):
        return self.registry.get_source(source_id)

    def _learn_optional_session_decision(
        self,
        source_id: str,
        source_name: str,
        base_metadata: dict[str, Any],
    ) -> SourceAccessDecision:
        session_status = self.sessions.status_for_source(source_id)
        session_usable = bool(session_status.usable)
        session_verified = bool(session_status.verified_at)
        session_state_path = session_status.storage_state_path if session_usable else ""
        metadata = {
            **base_metadata,
            "source_access_session_used": session_usable,
            "source_access_session_scope": session_status.session_scope,
            "source_access_session_status": session_status.status,
            "source_access_session_label": session_status.label,
            "source_access_session_verified": session_verified,
        }
        if session_usable:
            message = f"Using connected source session for {session_status.session_scope or source_name}."
            return SourceAccessDecision(
                can_execute=True,
                status="not_required",
                message=message,
                session_required=False,
                session_usable=True,
                session_verified=session_verified,
                session_scope=session_status.session_scope,
                session_state_path=session_state_path,
                metadata=metadata,
            )
        return SourceAccessDecision(
            can_execute=True,
            status="not_required",
            message="Source access session is not required.",
            session_required=False,
            session_scope=session_status.session_scope,
            metadata=metadata,
        )

    def _readiness_decision(
        self,
        source_id: str,
        *,
        source_name: str,
        source_url: str,
        session_required: bool,
        session_status: SourceSessionStatus,
        session_verified: bool,
        session_state_path: str,
        metadata: dict[str, Any],
    ) -> SourceAccessDecision | None:
        if not source_id:
            return None
        try:
            from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService

            service = SourceExecutionReadinessService(self.root)
            saved = service.load(source_id)
            readiness = service.evaluate(source_id)
        except Exception as exc:
            message = f"Saved source readiness could not be checked before execution: {exc}"
            return SourceAccessDecision(
                can_execute=False,
                status="blocked",
                message=message,
                blockers=[message],
                session_required=session_required,
                session_usable=bool(session_status.usable),
                session_verified=session_verified,
                session_scope=session_status.session_scope,
                session_state_path=session_state_path,
                metadata={**metadata, "source_access_status": "blocked", "source_url": source_url},
            )
        if not saved.last_checked_at:
            return None
        if readiness.readiness_status == "ready":
            return None
        detail = readiness.blockers[0] if readiness.blockers else readiness.readiness_summary
        message = (
            f"Saved source readiness is {readiness.readiness_status}; {detail} "
            "Rerun the safe source test or refresh source access before this source is used."
        )
        return SourceAccessDecision(
            can_execute=False,
            status="blocked",
            message=message,
            blockers=[message],
            session_required=session_required,
            session_usable=bool(session_status.usable),
            session_verified=session_verified,
            session_scope=session_status.session_scope,
            session_state_path=session_state_path,
            metadata={**metadata, "source_access_status": "blocked", "readiness_status": readiness.readiness_status},
        )


def _normalize_purpose(value: str) -> str:
    purpose = str(value or "").strip() or "daily_run"
    if purpose not in SOURCE_ACCESS_PURPOSES:
        return "daily_run"
    return purpose


def _saved_session_requirements(readiness: Any | None) -> tuple[bool, str, str]:
    checks = getattr(readiness, "checks", {}) or {}
    if not isinstance(checks, dict):
        return False, "", ""
    return (
        bool(checks.get("source_session_required")),
        str(checks.get("source_session_scope") or ""),
        str(checks.get("source_access_setup_hint") or ""),
    )


def _project_readiness_decision(
    readiness: Any | None,
    *,
    session_required: bool,
    session_status: SourceSessionStatus,
    session_verified: bool,
    session_state_path: str,
    metadata: dict[str, Any],
) -> SourceAccessDecision | None:
    if not readiness or not getattr(readiness, "last_checked_at", ""):
        return None
    readiness_status = str(getattr(readiness, "readiness_status", "") or "")
    if readiness_status == "ready":
        return None
    blockers = list(getattr(readiness, "blockers", []) or [])
    detail = blockers[0] if blockers else str(getattr(readiness, "readiness_summary", "") or "")
    message = (
        f"Saved source readiness is {readiness_status or 'unknown'}; {detail} "
        "Rerun the safe source test or refresh source access before this source is used."
    )
    return SourceAccessDecision(
        can_execute=False,
        status="blocked",
        message=message,
        blockers=[message],
        session_required=session_required,
        session_usable=bool(session_status.usable),
        session_verified=session_verified,
        session_scope=session_status.session_scope,
        session_state_path=session_state_path,
        metadata={**metadata, "source_access_status": "blocked", "readiness_status": readiness_status},
    )


def _needs_login_message(source_name: str, session_status: SourceSessionStatus) -> str:
    return (
        f"{session_status.session_scope or source_name} requires a connected session; "
        f"current source session status is {session_status.label}. {session_status.summary}"
    )


def _source_id(source: Any) -> str:
    if isinstance(source, dict):
        return str(source.get("source_id") or source.get("id") or "").strip()
    return str(getattr(source, "id", "") or getattr(source, "source_id", "") or "").strip()


def _source_name(source: Any, fallback: str = "") -> str:
    if isinstance(source, dict):
        return str(source.get("name") or fallback or "Unknown source")
    return str(getattr(source, "name", "") or fallback or "Unknown source")


def _source_url(source: Any) -> str:
    if isinstance(source, dict):
        return str(source.get("url") or source.get("path") or "")
    return str(getattr(source, "url", "") or getattr(source, "path", "") or "")


def _source_recipe_path(source: Any) -> str:
    if isinstance(source, dict):
        return str(source.get("recipe_path") or "").strip()
    return str(getattr(source, "recipe_path", "") or "").strip()
