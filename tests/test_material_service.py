from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.io.json_store import read_json
from job_agent.services.material_service import MaterialService, MaterialUpdate
from tests.helpers import write_sample_package


def test_save_job_materials_writes_files_and_marks_generated(project_root: Path) -> None:
    write_sample_package(project_root)

    MaterialService(project_root).save_job_materials(
        "stable-1",
        MaterialUpdate(
            cv="updated cv", application="updated app", form_answers="updated forms", match_analysis="updated analysis"
        ),
    )

    package = MaterialService(project_root).packages.find_package("stable-1")
    files = MaterialService(project_root).packages.read_package_files(package)
    assert files["cv"] == "updated cv"
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
    assert "SAP ABAP Consultant" in files["cv"]
    assert "Standard Form Answer Package" in files["form_answers"]
