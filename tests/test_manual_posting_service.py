from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.io.json_store import read_json
from job_agent.io.yaml_store import read_yaml
from job_agent.services.ai_search_service import AiSearchEvaluation
from job_agent.services.manual_posting_service import ManualPostingInput, ManualPostingService


def test_manual_posting_validates_title_or_description(project_root: Path) -> None:
    with pytest.raises(ValueError, match="title or pasted job description"):
        ManualPostingService(project_root).validate(ManualPostingInput())


def test_manual_posting_saves_yaml_and_creates_manual_job_metadata(project_root: Path) -> None:
    service = ManualPostingService(project_root)
    job = service.to_job(
        ManualPostingInput(
            title="SAP ABAP Consultant",
            source="Recruiter Mail",
            company="Client",
            description="ABAP RAP OData role",
            posted_date="2026-05-07",
        )
    )
    service._append_manual_job(job)

    data = read_yaml(project_root / "jobs" / "manual" / "manual_jobs.yaml", {})
    assert data["jobs"][0]["title"] == "SAP ABAP Consultant"
    assert data["jobs"][0]["source"] == "Recruiter Mail"
    assert data["jobs"][0]["source_confidence"] == "manual"
    assert data["jobs"][0]["freshness_confidence"] == "manual"


def test_manual_import_creates_placeholder_package_by_default(template_project: Path) -> None:
    result = ManualPostingService(template_project).import_posting(
        ManualPostingInput(title="SAP ABAP Consultant", description="ABAP RAP CDS OData Gateway")
    )

    index = read_json(Path(result.package_paths["index"]), {})
    assert result.material_status == "missing"
    assert index["material_status"] == "missing"
    assert index["materials_generated"] is False
    assert index["source_url"] == ""
    assert index["run_id"] == result.run.run_id


def test_manual_import_with_material_generation_creates_generated_package(template_project: Path) -> None:
    result = ManualPostingService(template_project).import_posting(
        ManualPostingInput(
            title="SAP ABAP Consultant",
            description="ABAP RAP CDS OData Gateway",
            generate_materials=True,
        )
    )

    index = read_json(Path(result.package_paths["index"]), {})
    assert result.material_status == "generated"
    assert index["material_status"] == "generated"
    assert Path(result.package_paths["cv"]).exists()


def test_manual_import_with_ai_evaluation_stores_ai_fields(
    monkeypatch: pytest.MonkeyPatch, template_project: Path
) -> None:
    class FakeAiSearchService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def is_configured(self) -> bool:
            return True

        def evaluate(self, *args, **kwargs) -> AiSearchEvaluation:
            return AiSearchEvaluation(
                status="evaluated",
                summary="Manual role is relevant.",
                recommended_angle="Lead with ABAP.",
                fit_confidence="high",
                risk_flags=["Confirm rate"],
                key_profile_evidence=["ABAP"],
                should_prioritize=True,
                model="fake-model",
            )

    monkeypatch.setattr("job_agent.services.manual_posting_service.AiSearchService", FakeAiSearchService)

    result = ManualPostingService(template_project).import_posting(
        ManualPostingInput(
            title="SAP ABAP Consultant",
            description="ABAP RAP CDS OData Gateway",
            ai_enhanced_search=True,
        )
    )

    index = read_json(Path(result.package_paths["index"]), {})
    assert index["ai_evaluation_status"] == "evaluated"
    assert index["ai_summary"] == "Manual role is relevant."
    assert index["ai_should_prioritize"] is True


def test_manual_import_missing_claude_key_skips_ai_without_failing(template_project: Path) -> None:
    result = ManualPostingService(template_project).import_posting(
        ManualPostingInput(
            title="SAP ABAP Consultant",
            description="ABAP RAP CDS OData Gateway",
            ai_enhanced_search=True,
        )
    )

    index = read_json(Path(result.package_paths["index"]), {})
    assert result.run.status == "completed"
    assert index["ai_evaluation_status"] == "skipped"
