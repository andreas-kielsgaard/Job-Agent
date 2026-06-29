from __future__ import annotations

from pathlib import Path

import pytest
from tests.helpers import write_sample_package

from job_agent.io.json_store import read_json, write_json
from job_agent.services.material_service import MaterialService, MaterialUpdate


def test_save_job_materials_writes_files_and_marks_generated(project_root: Path) -> None:
    write_sample_package(project_root)

    MaterialService(project_root).save_job_materials(
        "stable-1",
        MaterialUpdate(
            cv="updated cv",
            focused_cv="updated focused cv",
            application="updated app",
            form_answers="updated forms",
            match_analysis="updated analysis",
        ),
    )

    package = MaterialService(project_root).packages.find_package("stable-1")
    files = MaterialService(project_root).packages.read_package_files(package)
    assert files["cv"] == "updated cv"
    assert files["focused_cv"] == "updated focused cv"
    assert files["application"] == "updated app"
    assert files["form_answers"] == "updated forms"
    assert files["match_analysis"] == "updated analysis"
    index = read_json(Path(package["_index_path"]), {})
    assert index["materials_generated"] is True
    assert index["material_status"] == "generated"


def test_save_job_materials_missing_package_raises(project_root: Path) -> None:
    with pytest.raises(KeyError):
        MaterialService(project_root).save_job_materials("missing", MaterialUpdate())


def test_generate_job_materials_detects_missing_required_files(template_project: Path) -> None:
    write_sample_package(template_project)
    package = MaterialService(template_project).packages.find_package("stable-1")
    Path(package["paths"]["match"]).unlink()

    with pytest.raises(ValueError, match="missing required files"):
        MaterialService(template_project).generate_job_materials("stable-1", use_llm=False)


def test_generate_job_materials_deterministically_regenerates_package(template_project: Path) -> None:
    write_sample_package(template_project)

    refreshed = MaterialService(template_project).generate_job_materials("stable-1", use_llm=False)

    assert refreshed["materials_generated"] is True
    assert refreshed["material_status"] == "generated"
    package = MaterialService(template_project).packages.find_package("stable-1")
    files = MaterialService(template_project).packages.read_package_files(package)
    assert "Saved Posting Snapshot" in files["posting_snapshot"]
    assert "SAP ABAP Consultant" in files["posting_snapshot"]
    assert "SAP ABAP Consultant" in files["cv"]
    assert "Focused One-Page CV" in files["focused_cv"]
    assert "<!doctype html>" in files["focused_cv_html"]
    assert "\\documentclass" in files["focused_cv_tex"]
    assert "Standard Form Answer Package" in files["form_answers"]
    assert "cv-one-page.pdf" in files["form_answers"]
    assert Path(package["paths"]["focused_cv_pdf"]).read_bytes().startswith(b"%PDF")


def test_generate_job_materials_preserves_ai_match_projection(template_project: Path) -> None:
    paths = write_sample_package(template_project)
    index_path = Path(paths["index"])
    index = read_json(index_path, {})
    index.update(
        {
            "ai_evaluation_status": "evaluated",
            "ai_match_score": 94,
            "ai_summary": "Strong AI fit.",
        }
    )
    write_json(index_path, index)

    refreshed = MaterialService(template_project).generate_job_materials("stable-1", use_llm=False)

    assert refreshed["ai_match_score"] == 94
    assert refreshed["deterministic_match_score"] == 82
    assert refreshed["match_score"] == 88
    assert refreshed["ai_summary"] == "Strong AI fit."


def test_generate_many_continues_after_failure(template_project: Path) -> None:
    write_sample_package(template_project, stable_id="stable-1")
    write_sample_package(template_project, stable_id="stable-2", title="SAP RAP Consultant")

    result = MaterialService(template_project).generate_many(["stable-1", "missing", "stable-2"], use_llm=False)

    assert result.total == 3
    assert result.succeeded == 2
    assert result.failed == 1
    assert result.failures[0].job_id == "missing"
    for stable_id in ["stable-1", "stable-2"]:
        package = MaterialService(template_project).packages.find_package(stable_id)
        index = read_json(Path(package["_index_path"]), {})
        assert index["materials_generated"] is True
        assert index["material_status"] == "generated"


def test_external_application_prompt_round_trip_updates_package(template_project: Path) -> None:
    write_sample_package(template_project)
    service = MaterialService(template_project)

    prepared = service.prepare_external_application_generation("stable-1")
    refreshed = service.apply_external_application_generation(
        "stable-1",
        prepared["interaction_id"],
        "External application text",
    )

    assert "SAP ABAP Consultant" in prepared["prompt"]
    assert refreshed["materials_generated"] is True
    assert refreshed["material_status"] == "generated"
    assert refreshed["external_agent_application_interaction_id"] == prepared["interaction_id"]
    package = service.packages.find_package("stable-1")
    files = service.packages.read_package_files(package)
    assert files["application"] == "External application text\n"
    assert "Standard Form Answer Package" in files["form_answers"]
