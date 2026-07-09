from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.services.recipe_candidate_service import RecipeCandidate, RecipeCandidateStore
from job_agent.services.recipes.mapping import load_project_job_board_recipe
from job_agent.services.source_access_gate_service import SourceAccessGateService
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_registry_service import SourceRegistryService

SOURCE_RECIPE_EVIDENCE_STATUSES = {
    "ready",
    "unknown",
    "needs_relearn_with_session",
    "needs_retest_with_session",
    "needs_detail_relearn",
    "blocked",
}


@dataclass
class SourceRecipeEvidenceDecision:
    can_trust_recipe: bool
    status: str
    message: str
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommended_action: str = ""
    action_href: str = ""
    source_id: str = ""
    recipe_path: str = ""
    learner_access_evidence: dict[str, Any] = field(default_factory=dict)
    learner_detail_evidence: dict[str, Any] = field(default_factory=dict)
    detail_quality: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceRecipeEvidenceGateService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.registry = SourceRegistryService(self.root)
        self.candidates = RecipeCandidateStore(self.root)
        self.readiness = SourceExecutionReadinessService(self.root)
        self.access_gate = SourceAccessGateService(self.root)

    def evaluate(self, source_id: str, *, purpose: str = "detail_ingest") -> SourceRecipeEvidenceDecision:
        source_id = source_id.strip()
        source = self.registry.get_source(source_id)
        if not source:
            return _decision(
                status="blocked",
                message=f"Source not found: {source_id or 'unknown source'}.",
                source_id=source_id,
                blockers=[f"Source not found: {source_id or 'unknown source'}."],
            )
        recipe_path = str(getattr(source, "recipe_path", "") or "").strip()
        if not recipe_path:
            return _decision(
                status="unknown",
                message="Source has no selected reading plan, so recipe evidence cannot be checked.",
                source_id=source_id,
                recipe_path=recipe_path,
                warnings=["Source has no selected reading plan."],
                action_href=f"/sources/{source_id}",
            )

        access_decision = self.access_gate.evaluate_source(source, purpose=purpose, source_id=source_id)
        session_required = _recipe_requires_session(self.root, recipe_path)
        session_usable = bool(getattr(access_decision, "session_usable", False))
        candidate = self._latest_adopted_candidate(source_id, recipe_path)
        readiness = self.readiness.evaluate(source_id)
        detail_quality = _detail_quality_from_readiness(readiness.checks)
        metadata = {
            "access_status": getattr(access_decision, "status", ""),
            "access_can_execute": bool(getattr(access_decision, "can_execute", False)),
            "session_required": session_required,
            "session_usable": session_usable,
            "readiness_status": readiness.readiness_status,
            "candidate_id": candidate.candidate_id if candidate else "",
        }

        if not candidate:
            return _decision(
                status="unknown",
                message="No adopted recipe candidate evidence is available for the current reading plan.",
                source_id=source_id,
                recipe_path=recipe_path,
                warnings=["Current reading plan has no linked learner evidence."],
                action_href=f"/sources/{source_id}",
                detail_quality=detail_quality,
                metadata=metadata,
            )

        learner_access = dict(candidate.learner_access_evidence or {})
        learner_detail = dict(candidate.learner_detail_evidence or {})
        if not learner_access:
            return _decision(
                status="unknown",
                message="The adopted reading plan predates learner access evidence.",
                source_id=source_id,
                recipe_path=recipe_path,
                warnings=["Relearn this source to record whether the plan was captured with a session."],
                action_href=f"/sources/{source_id}",
                learner_access_evidence=learner_access,
                learner_detail_evidence=learner_detail,
                detail_quality=detail_quality,
                metadata=metadata,
            )

        learner_used_session = bool(learner_access.get("session_used") or learner_access.get("source_session_used"))
        source_test_used_session = bool(readiness.checks.get("source_access_session_used"))
        poor_detail_quality = detail_quality.get("status") in {"missing", "headline_only"}
        login_gated = bool(readiness.checks.get("source_access_login_gate_detected"))

        if session_required and not learner_used_session:
            return _needs_relearn_with_session(
                source_id,
                recipe_path,
                learner_access,
                learner_detail,
                detail_quality,
                metadata,
                session_usable=session_usable,
                message="This reading plan requires a source session, but learner evidence was captured without one.",
            )
        if session_usable and not learner_used_session and (poor_detail_quality or login_gated):
            return _needs_relearn_with_session(
                source_id,
                recipe_path,
                learner_access,
                learner_detail,
                detail_quality,
                metadata,
                session_usable=session_usable,
                message=(
                    "A usable source session exists, but the current reading plan was learned without it "
                    "and detail evidence is weak."
                ),
            )
        if learner_used_session and not source_test_used_session:
            message = "The reading plan was learned with a source session, but the saved source test did not verify it."
            return _decision(
                status="needs_retest_with_session",
                message=message,
                source_id=source_id,
                recipe_path=recipe_path,
                blockers=[message],
                recommended_action="run_source_test_with_session",
                action_href=f"/sources/{source_id}/test-run?start=1",
                learner_access_evidence=learner_access,
                learner_detail_evidence=learner_detail,
                detail_quality=detail_quality,
                metadata=metadata,
            )
        if poor_detail_quality:
            message = "Saved source-test evidence found headline-only or missing job descriptions."
            return _decision(
                status="needs_detail_relearn",
                message=message,
                source_id=source_id,
                recipe_path=recipe_path,
                blockers=[message],
                recommended_action="relearn_detail_selectors",
                action_href=f"/sources/{source_id}",
                learner_access_evidence=learner_access,
                learner_detail_evidence=learner_detail,
                detail_quality=detail_quality,
                metadata=metadata,
            )
        return _decision(
            status="ready",
            message="Recipe evidence matches the current source access and detail-quality checks.",
            source_id=source_id,
            recipe_path=recipe_path,
            learner_access_evidence=learner_access,
            learner_detail_evidence=learner_detail,
            detail_quality=detail_quality,
            metadata=metadata,
        )

    def _latest_adopted_candidate(self, source_id: str, recipe_path: str) -> RecipeCandidate | None:
        matches = []
        for summary in self.candidates.list_candidates(status="approved"):
            try:
                candidate = self.candidates.load_candidate(summary.candidate_id)
            except ValueError:
                continue
            if candidate.adopted_source_id != source_id:
                continue
            if _normalize_path(candidate.adopted_recipe_path) != _normalize_path(recipe_path):
                continue
            matches.append(candidate)
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.adopted_at, reverse=True)[0]


def _needs_relearn_with_session(
    source_id: str,
    recipe_path: str,
    learner_access: dict[str, Any],
    learner_detail: dict[str, Any],
    detail_quality: dict[str, Any],
    metadata: dict[str, Any],
    *,
    session_usable: bool,
    message: str,
) -> SourceRecipeEvidenceDecision:
    return _decision(
        status="needs_relearn_with_session",
        message=message,
        source_id=source_id,
        recipe_path=recipe_path,
        blockers=[message],
        recommended_action="relearn_with_session" if session_usable else "connect_session",
        action_href=f"/sources/{source_id}" if session_usable else f"/sources/{source_id}/session",
        learner_access_evidence=learner_access,
        learner_detail_evidence=learner_detail,
        detail_quality=detail_quality,
        metadata=metadata,
    )


def _decision(
    *,
    status: str,
    message: str,
    source_id: str = "",
    recipe_path: str = "",
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    recommended_action: str = "",
    action_href: str = "",
    learner_access_evidence: dict[str, Any] | None = None,
    learner_detail_evidence: dict[str, Any] | None = None,
    detail_quality: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> SourceRecipeEvidenceDecision:
    return SourceRecipeEvidenceDecision(
        can_trust_recipe=status == "ready",
        status=status,
        message=message,
        blockers=list(blockers or []),
        warnings=list(warnings or []),
        recommended_action=recommended_action,
        action_href=action_href,
        source_id=source_id,
        recipe_path=recipe_path,
        learner_access_evidence=dict(learner_access_evidence or {}),
        learner_detail_evidence=dict(learner_detail_evidence or {}),
        detail_quality=dict(detail_quality or {}),
        metadata=dict(metadata or {}),
    )


def _recipe_requires_session(root: Path, recipe_path: str) -> bool:
    try:
        recipe = load_project_job_board_recipe(root, recipe_path)
    except (OSError, ValueError):
        return False
    return bool(recipe.access.requires_session)


def _detail_quality_from_readiness(checks: dict[str, Any]) -> dict[str, Any]:
    status = str(checks.get("detail_quality_status") or "").strip()
    if not status:
        return {}
    return {
        "status": status,
        "summary": str(checks.get("detail_quality_summary") or ""),
        "present_count": _int(checks.get("detail_description_present_count")),
        "distinct_count": _int(checks.get("detail_description_distinct_count")),
        "average_length": _int(checks.get("detail_average_description_length")),
    }


def _normalize_path(value: str) -> str:
    return str(value or "").replace("\\", "/").strip().strip("/")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
