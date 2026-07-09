from __future__ import annotations

from pathlib import Path

from job_agent.io.yaml_store import read_yaml
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_suggestion_service import RecipeSuggestionResult
from job_agent.services.source_execution_readiness_service import SourceExecutionReadinessService
from job_agent.services.source_recipe_evidence_gate_service import SourceRecipeEvidenceGateService
from job_agent.services.source_session_service import SourceSessionService
from job_agent.services.source_test_service import SourceTestJobPreview, SourceTestResult


def test_required_session_recipe_learned_without_session_needs_relearn(project_root: Path) -> None:
    recipe_path = _write_source(project_root, requires_session=True)
    _adopt_candidate(project_root, recipe_path, learner_session_used=False)
    _save_readiness(project_root, detail_quality_status="good")

    decision = SourceRecipeEvidenceGateService(project_root).evaluate("dice")

    assert decision.can_trust_recipe is False
    assert decision.status == "needs_relearn_with_session"
    assert decision.recommended_action == "connect_session"
    assert decision.action_href == "/sources/dice/session"


def test_optional_session_with_poor_details_needs_relearn_with_session(project_root: Path) -> None:
    recipe_path = _write_source(project_root, requires_session=False)
    _write_connected_session(project_root)
    _adopt_candidate(project_root, recipe_path, learner_session_used=False)
    _save_readiness(project_root, detail_quality_status="headline_only")

    decision = SourceRecipeEvidenceGateService(project_root).evaluate("dice")

    assert decision.status == "needs_relearn_with_session"
    assert decision.recommended_action == "relearn_with_session"
    assert decision.action_href == "/sources/dice"


def test_session_learner_without_session_source_test_needs_retest(project_root: Path) -> None:
    recipe_path = _write_source(project_root, requires_session=True)
    _write_connected_session(project_root, verified=True)
    _adopt_candidate(project_root, recipe_path, learner_session_used=True)
    _save_readiness(project_root, detail_quality_status="good", source_test_session_used=False)

    decision = SourceRecipeEvidenceGateService(project_root).evaluate("dice")

    assert decision.status == "needs_retest_with_session"
    assert decision.recommended_action == "run_source_test_with_session"
    assert decision.action_href == "/sources/dice/test-run?start=1"


def test_headline_only_details_need_detail_relearn(project_root: Path) -> None:
    recipe_path = _write_source(project_root, requires_session=False)
    _adopt_candidate(project_root, recipe_path, learner_session_used=False)
    _save_readiness(project_root, detail_quality_status="headline_only")

    decision = SourceRecipeEvidenceGateService(project_root).evaluate("dice")

    assert decision.status == "needs_detail_relearn"
    assert decision.recommended_action == "relearn_detail_selectors"
    assert decision.action_href == "/sources/dice"


def test_aligned_session_and_detail_evidence_is_ready(project_root: Path) -> None:
    recipe_path = _write_source(project_root, requires_session=True)
    _write_connected_session(project_root, verified=True)
    _adopt_candidate(project_root, recipe_path, learner_session_used=True)
    _save_readiness(project_root, detail_quality_status="good", source_test_session_used=True)

    decision = SourceRecipeEvidenceGateService(project_root).evaluate("dice")

    assert decision.can_trust_recipe is True
    assert decision.status == "ready"
    assert decision.detail_quality["status"] == "good"
    assert decision.learner_access_evidence["session_used"] is True


def test_legacy_adopted_recipe_without_evidence_is_unknown(project_root: Path) -> None:
    recipe_path = _write_source(project_root, requires_session=False)
    _adopt_candidate(project_root, recipe_path, learner_access_evidence={})
    _save_readiness(project_root, detail_quality_status="good")

    decision = SourceRecipeEvidenceGateService(project_root).evaluate("dice")

    assert decision.can_trust_recipe is False
    assert decision.status == "unknown"
    assert decision.warnings


def _write_source(project_root: Path, *, requires_session: bool) -> str:
    recipe_path = project_root / "sources" / "recipes" / "dice.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    access_yaml = f"access:\n  requires_session: {'true' if requires_session else 'false'}\n  session_scope: dice.com\n"
    recipe_path.write_text(
        "source_name: Dice\n"
        "mode: static_html\n"
        f"{access_yaml}"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a\n"
        "  link_selector: a\n"
        "detail:\n"
        "  follow: true\n"
        "  description_selector: .description\n",
        encoding="utf-8",
    )
    (project_root / "sources" / "source-registry.yaml").write_text(
        "sources:\n"
        "  - id: dice\n"
        "    name: Dice\n"
        "    kind: recipe\n"
        "    status: testing\n"
        "    url: https://dice.com/jobs\n"
        "    recipe_path: sources/recipes/dice.yaml\n"
        "    enabled: false\n"
        "    notes: Test source.\n"
        "    tags: []\n",
        encoding="utf-8",
    )
    (project_root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n"
        "  - name: Dice\n"
        "    source_id: dice\n"
        "    type: recipe_html\n"
        "    url: https://dice.com/jobs\n"
        "    recipe_path: sources/recipes/dice.yaml\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    return "sources/recipes/dice.yaml"


def _adopt_candidate(
    project_root: Path,
    recipe_path: str,
    *,
    learner_session_used: bool = False,
    learner_access_evidence: dict | None = None,
) -> None:
    artifact = project_root / "output" / "recipe-calibration" / "dice"
    artifact.mkdir(parents=True, exist_ok=True)
    store = RecipeCandidateStore(project_root)
    candidate = store.save_candidate_from_suggestion(
        RecipeSuggestionResult(
            source_name="Dice",
            start_url="https://dice.com/jobs",
            artifact_dir=artifact,
            suggested_recipe_yaml="source_name: Dice\nmode: static_html\n",
            schema_valid=True,
            selected_strategy="selector_based",
        )
    )
    candidate = store.approve_candidate(candidate.candidate_id, recipe_path=recipe_path, source_id="dice")
    store.adopt_candidate(candidate.candidate_id, source_id="dice", recipe_path=recipe_path)
    if learner_access_evidence is None:
        learner_access_evidence = {
            "session_used": learner_session_used,
            "session_scope": "dice.com" if learner_session_used else "",
            "session_usable": learner_session_used,
        }
    store.update_candidate_evidence(
        candidate.candidate_id,
        learner_access_evidence=learner_access_evidence,
        learner_detail_evidence={"detail_sample_captured": True, "source_session_used": learner_session_used},
    )


def _save_readiness(
    project_root: Path,
    *,
    detail_quality_status: str,
    source_test_session_used: bool = False,
) -> None:
    SourceExecutionReadinessService(project_root).save_from_source_test(
        SourceTestResult(
            source_id="dice",
            source_name="Dice",
            source_type="recipe_html",
            source_enabled=False,
            forced_disabled=True,
            status="success",
            job_count=1,
            jobs=[
                SourceTestJobPreview(
                    title="SAP ABAP Consultant",
                    url="https://dice.com/job/1",
                    source="Dice",
                    source_id="dice",
                )
            ],
            source_access_session_used=source_test_session_used,
            source_access_session_status="connected" if source_test_session_used else "",
            source_access_session_scope="dice.com" if source_test_session_used else "",
            detail_quality_status=detail_quality_status,
            detail_quality_summary=f"Detail quality is {detail_quality_status}.",
        )
    )
    saved = read_yaml(project_root / "sources" / "source-execution-readiness.yaml", {})
    assert saved["sources"]["dice"]["checks"]["detail_quality_status"] == detail_quality_status


def _write_connected_session(project_root: Path, *, verified: bool = False) -> None:
    state_path = project_root / "sources" / "sessions" / "dice.storage-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"cookies": [{"name": "sid", "value": "abc", "domain": "dice.com", "path": "/"}]}',
        encoding="utf-8",
    )
    sessions = SourceSessionService(project_root)
    sessions.record_storage_state(
        "dice",
        session_scope="dice.com",
        storage_state_path="sources/sessions/dice.storage-state.json",
    )
    if verified:
        sessions.mark_verified("dice", session_scope="dice.com")
