from __future__ import annotations

from pathlib import Path

from job_agent.profile_contract import build_profile_contract


def test_profile_contract_reports_sections_and_diagnostics(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "contact.yaml").write_text("contact:\n  name: Test User\n", encoding="utf-8")
    (profile / "canonical-cv.md").write_text("ABAP profile\n", encoding="utf-8")
    (profile / "skills.yaml").write_text("skills:\n  strongest: []\n", encoding="utf-8")
    (profile / "experience.yaml").write_text("experience: []\n", encoding="utf-8")
    (profile / "preferences.yaml").write_text(
        "thresholds:\n  highlight_score: 80\nrole_preferences:\n  preferred_contract_types:\n    - contract\n",
        encoding="utf-8",
    )

    report = build_profile_contract(tmp_path, {"filename": "reference-cv.pdf", "extraction_error": "broken pdf"})

    assert report["status"] == "attention"
    assert report["section_data_count"] >= 1
    assert report["section_empty_count"] >= 1
    assert {section["key"] for section in report["sections"]} >= {"identity", "skill_matrix", "cv_evidence"}
    assert any(item["severity"] == "error" and "CV text extraction failed" in item["title"] for item in report["diagnostics"])
    assert any("Advanced thresholds" in item["title"] for item in report["diagnostics"])
