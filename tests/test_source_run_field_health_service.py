from __future__ import annotations

from datetime import date
from pathlib import Path

from tests.helpers import EURSAP_SOURCE, seed_eursap_source, seed_source_registry

from job_agent.digest import write_placeholder_job_package
from job_agent.models import Job, MatchResult
from job_agent.run_store import RunOptions, RunStore
from job_agent.services.source_run_field_health_service import (
    SourceRunFieldHealthService,
    evaluate_source_run_field_health,
)


def test_source_run_field_health_flags_headline_only_descriptions(project_root: Path) -> None:
    seed_eursap_source(project_root)
    _write_detail_recipe(project_root)

    record = evaluate_source_run_field_health(
        source_id="eursap-jobs",
        source_name="Eursap Jobs",
        run_id="run-1",
        recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
        root=project_root,
        jobs=[
            Job(
                title="SAP ABAP Consultant",
                source="Eursap Jobs",
                source_id="eursap-jobs",
                url="https://eursap.eu/jobs/1",
                description="SAP ABAP Consultant",
            )
        ],
    )

    assert record.status == "needs_relearn"
    assert record.required_missing_fields == ["description"]
    assert record.description_strong_count == 0
    assert record.recommended_action == "reset_learned_state"


def test_source_run_field_health_requires_descriptions_for_old_recipe_sources(project_root: Path) -> None:
    seed_eursap_source(project_root)

    record = evaluate_source_run_field_health(
        source_id="eursap-jobs",
        source_name="Eursap Jobs",
        run_id="run-1",
        recipe_path="sources/recipes/experimental/eursap-jobs.yaml",
        root=project_root,
        jobs=[
            Job(
                title="SAP ABAP Consultant",
                source="Eursap Jobs",
                source_id="eursap-jobs",
                url="https://eursap.eu/jobs/1",
                description="SAP ABAP Consultant",
            )
        ],
    )

    assert record.status == "needs_relearn"
    assert "description" in record.expected_fields
    assert record.required_missing_fields == ["description"]


def test_source_run_field_health_refreshes_from_latest_packages(project_root: Path) -> None:
    seed_eursap_source(project_root)
    _write_detail_recipe(project_root)
    write_placeholder_job_package(
        Job(
            title="SAP Basis Consultant",
            source="Eursap Jobs",
            source_id="eursap-jobs",
            url="https://eursap.eu/jobs/1",
            description=(
                "Detailed Basis contract role with enough SAP migration, operations, upgrade, monitoring, "
                "and stakeholder coordination context for review."
            ),
        ),
        MatchResult(total_score=70, category="strong"),
        date(2026, 6, 29),
        root=project_root,
        run_id="20260629-100000-run",
        stable_id="stable-1",
        fuzzy_key="fuzzy-1",
        state="new",
    )

    record = SourceRunFieldHealthService(project_root).refresh_from_latest_packages("eursap-jobs")

    assert record.status == "healthy"
    assert record.run_id == "20260629-100000-run"
    assert record.description_strong_count == 1
    assert SourceRunFieldHealthService(project_root).get("eursap-jobs").status == "healthy"


def test_source_run_field_health_refreshes_latest_completed_daily_run(project_root: Path) -> None:
    seed_eursap_source(project_root)
    _write_detail_recipe(project_root)
    store = RunStore(project_root)
    older = store.create_run(RunOptions())
    newer = store.create_run(RunOptions())
    store.update(older.run_id, status="completed", started_at="2026-06-28T10:00:00+00:00")
    store.update(newer.run_id, status="completed", started_at="2026-06-29T10:00:00+00:00")
    write_placeholder_job_package(
        Job(
            title="SAP Basis Consultant",
            source="Eursap Jobs",
            source_id="eursap-jobs",
            url="https://eursap.eu/jobs/older",
            description=(
                "Detailed Basis contract role with enough SAP migration, operations, upgrade, monitoring, "
                "and stakeholder coordination context for review."
            ),
        ),
        MatchResult(total_score=70, category="strong"),
        date(2026, 6, 28),
        root=project_root,
        run_id=older.run_id,
        stable_id="stable-older",
        fuzzy_key="fuzzy-older",
        state="new",
    )
    write_placeholder_job_package(
        Job(
            title="SAP ABAP Consultant",
            source="Eursap Jobs",
            source_id="eursap-jobs",
            url="https://eursap.eu/jobs/newer",
            description="SAP ABAP Consultant",
        ),
        MatchResult(total_score=70, category="strong"),
        date(2026, 6, 29),
        root=project_root,
        run_id=newer.run_id,
        stable_id="stable-newer",
        fuzzy_key="fuzzy-newer",
        state="new",
    )

    result = SourceRunFieldHealthService(project_root).refresh_latest_daily_run()

    assert result.run_id == newer.run_id
    assert result.status == "needs_action"
    assert result.checked_source_count == 1
    assert result.needs_action_count == 1
    assert SourceRunFieldHealthService(project_root).get("eursap-jobs").run_id == newer.run_id
    assert SourceRunFieldHealthService(project_root).get("eursap-jobs").status == "needs_relearn"


def test_source_run_field_health_is_not_applicable_without_recipe(project_root: Path) -> None:
    seed_source_registry(project_root, {**EURSAP_SOURCE, "kind": "job_board", "recipe_path": ""})

    record = SourceRunFieldHealthService(project_root).update_from_jobs(
        "eursap-jobs",
        source_name="Eursap Jobs",
        jobs=[Job(title="SAP ABAP Consultant", source_id="eursap-jobs", url="https://example.com/1")],
        run_id="run-1",
    )

    assert record.status == "not_applicable"
    assert SourceRunFieldHealthService(project_root).get("eursap-jobs").status == "unknown"


def _write_detail_recipe(root: Path) -> None:
    path = root / "sources" / "recipes" / "experimental" / "eursap-jobs.yaml"
    path.write_text(
        "source_name: Eursap Jobs\n"
        "start_url: https://eursap.eu/jobs\n"
        "mode: static_html\n"
        "listing:\n"
        "  card_selector: article\n"
        "  title_selector: h2\n"
        "  link_selector: a\n"
        "detail:\n"
        "  follow: true\n"
        "  description_selector: .description\n",
        encoding="utf-8",
    )
