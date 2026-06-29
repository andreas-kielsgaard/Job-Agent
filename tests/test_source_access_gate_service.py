from __future__ import annotations

from pathlib import Path

from job_agent.services.source_access_gate_service import SourceAccessGateService
from job_agent.services.source_execution_readiness_service import SourceExecutionReadiness
from job_agent.services.source_session_service import SourceSessionService


def test_gate_allows_source_without_session_requirement(project_root: Path) -> None:
    source = _write_recipe_source(project_root, requires_session=False)

    decision = SourceAccessGateService(project_root).evaluate_source(source, purpose="daily_run")

    assert decision.can_execute is True
    assert decision.status == "not_required"
    assert decision.session_required is False
    assert decision.session_state_path == ""


def test_gate_blocks_unknown_source_id(project_root: Path) -> None:
    decision = SourceAccessGateService(project_root).evaluate("missing-source", purpose="daily_run")

    assert decision.can_execute is False
    assert decision.status == "blocked"
    assert decision.blockers == ["Source not found: missing-source."]
    assert decision.metadata["source_access_status"] == "blocked"


def test_gate_reports_required_missing_expired_and_missing_state_sessions(project_root: Path) -> None:
    source = _write_recipe_source(project_root, requires_session=True)
    service = SourceAccessGateService(project_root)

    missing = service.evaluate_source(source, purpose="source_test")
    assert missing.can_execute is False
    assert missing.status == "needs_login"
    assert missing.session_required is True
    assert "requires a connected session" in missing.message

    SourceSessionService(project_root).record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path="sources/sessions/sample-source.storage-state.json",
        expires_at="2000-01-01T00:00:00+00:00",
    )
    expired = service.evaluate_source(source, purpose="source_test")
    assert expired.can_execute is False
    assert expired.status == "needs_login"
    assert "Expired" in expired.message

    SourceSessionService(project_root).record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path="sources/sessions/missing.storage-state.json",
    )
    missing_state = service.evaluate_source(source, purpose="source_test")
    assert missing_state.can_execute is False
    assert missing_state.status == "needs_login"
    assert "Session file missing" in missing_state.message


def test_gate_allows_unverified_session_for_source_test_but_not_execution(project_root: Path) -> None:
    source = _write_recipe_source(project_root, requires_session=True)
    _write_session_state(project_root)
    SourceSessionService(project_root).record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path="sources/sessions/sample-source.storage-state.json",
    )
    service = SourceAccessGateService(project_root)

    source_test = service.evaluate_source(source, purpose="source_test")
    daily_run = service.evaluate_source(source, purpose="daily_run")

    assert source_test.can_execute is True
    assert source_test.status == "ready"
    assert source_test.session_usable is True
    assert source_test.session_verified is False
    assert daily_run.can_execute is False
    assert daily_run.status == "needs_verification"
    assert "not verified" in daily_run.message


def test_gate_blocks_execution_when_saved_readiness_is_stale(project_root: Path) -> None:
    source = _write_recipe_source(project_root, requires_session=True)
    _write_registry_projection_and_stale_readiness(project_root)
    _write_session_state(project_root)
    session_service = SourceSessionService(project_root)
    session_service.record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path="sources/sessions/sample-source.storage-state.json",
    )
    session_service.mark_verified("sample-source", session_scope="example.com")

    decision = SourceAccessGateService(project_root).evaluate_source(source, purpose="daily_run")

    assert decision.can_execute is False
    assert decision.status == "blocked"
    assert "Saved source readiness is blocked" in decision.message
    assert "Reading plan changed since the saved source test" in decision.message


def test_gate_selects_optional_connected_session(project_root: Path) -> None:
    source = _write_recipe_source(project_root, requires_session=False)
    _write_session_state(project_root)
    SourceSessionService(project_root).record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path="sources/sessions/sample-source.storage-state.json",
    )

    decision = SourceAccessGateService(project_root).evaluate_source(source, purpose="source_test")

    assert decision.can_execute is True
    assert decision.status == "not_required"
    assert decision.session_required is False
    assert decision.session_usable is True
    assert decision.session_state_path == "sources/sessions/sample-source.storage-state.json"


def test_gate_selects_connected_session_for_learning_when_recipe_file_is_missing(project_root: Path) -> None:
    source = {
        "source_id": "sample-source",
        "name": "Sample Source",
        "type": "recipe_html",
        "url": "https://example.com/jobs",
        "recipe_path": "sources/recipes/missing.yaml",
    }
    _write_session_state(project_root)
    SourceSessionService(project_root).record_storage_state(
        "sample-source",
        session_scope="example.com",
        storage_state_path="sources/sessions/sample-source.storage-state.json",
    )

    decision = SourceAccessGateService(project_root).evaluate_source(source, purpose="learn")

    assert decision.can_execute is True
    assert decision.status == "not_required"
    assert decision.session_required is False
    assert decision.session_usable is True
    assert decision.session_scope == "example.com"
    assert decision.session_state_path == "sources/sessions/sample-source.storage-state.json"


def test_gate_projects_saved_session_requirement_without_recomputing_readiness(project_root: Path) -> None:
    source = {
        "source_id": "sample-source",
        "name": "Sample Source",
        "type": "recipe_html",
        "url": "https://example.com/jobs",
        "recipe_path": "sources/recipes/missing.yaml",
    }
    readiness = SourceExecutionReadiness(
        source_id="sample-source",
        last_checked_at="2026-06-29T10:00:00+00:00",
        readiness_status="ready",
        checks={"source_session_required": True, "source_session_scope": "example.com"},
    )

    decision = SourceAccessGateService(project_root).project_source(source, purpose="daily_run", readiness=readiness)

    assert decision.can_execute is False
    assert decision.status == "needs_login"
    assert decision.session_required is True
    assert "requires a connected session" in decision.message


def _write_recipe_source(root: Path, *, requires_session: bool) -> dict[str, str]:
    recipe_path = root / "sources" / "recipes" / "sample-source.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    access_yaml = (
        f"access:\n  requires_session: {'true' if requires_session else 'false'}\n  session_scope: example.com\n"
    )
    recipe_path.write_text(
        "source_name: Sample Source\n"
        "mode: static_html\n"
        f"{access_yaml}"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a.job-link\n"
        "  link_selector: a.job-link\n",
        encoding="utf-8",
    )
    return {
        "source_id": "sample-source",
        "name": "Sample Source",
        "type": "recipe_html",
        "url": "https://example.com/jobs",
        "recipe_path": recipe_path.relative_to(root).as_posix(),
    }


def _write_session_state(root: Path) -> None:
    state_path = root / "sources" / "sessions" / "sample-source.storage-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"cookies": [{"name": "sid", "value": "abc", "domain": "example.com", "path": "/"}], "origins": []}',
        encoding="utf-8",
    )


def _write_registry_projection_and_stale_readiness(root: Path) -> None:
    (root / "sources" / "source-registry.yaml").write_text(
        "sources:\n"
        "  - id: sample-source\n"
        "    name: Sample Source\n"
        "    kind: recipe\n"
        "    status: testing\n"
        "    url: https://example.com/jobs\n"
        "    recipe_path: sources/recipes/sample-source.yaml\n",
        encoding="utf-8",
    )
    (root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n"
        "  - name: Sample Source\n"
        "    source_id: sample-source\n"
        "    type: recipe_html\n"
        "    url: https://example.com/jobs\n"
        "    recipe_path: sources/recipes/sample-source.yaml\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    (root / "sources" / "source-execution-readiness.yaml").write_text(
        "sources:\n"
        "  sample-source:\n"
        "    last_checked_at: '2000-01-01T00:00:00+00:00'\n"
        "    dry_run_status: success\n"
        "    dry_run_job_count: 1\n"
        "    dry_run_warning_count: 0\n"
        "    dry_run_warnings: []\n"
        "    dry_run_capability_checks: []\n"
        "    readiness_status: ready\n"
        "    readiness_summary: Ready.\n"
        "    checks: {}\n"
        "    blockers: []\n"
        "    warnings: []\n",
        encoding="utf-8",
    )
