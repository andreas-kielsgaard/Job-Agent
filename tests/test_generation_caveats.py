from __future__ import annotations

from pathlib import Path

from job_agent.config import load_profile
from job_agent.generator import generate_materials, select_experience
from job_agent.models import Job, MatchResult


def test_fiori_role_includes_fiori_caveat(template_project: Path) -> None:
    profile = load_profile(template_project)
    job = Job(title="SAP Fiori Backend Developer", description="Fiori UI5 with ABAP Gateway backend")
    match = MatchResult(total_score=65, category="exploratory")

    package = generate_materials(job, match, profile, use_llm=False, root=template_project)

    assert "not a pure UI5 expert" in package.application


def test_project_manager_role_includes_pm_caveat(template_project: Path) -> None:
    profile = load_profile(template_project)
    job = Job(title="SAP Project Manager", description="Project manager with SAP delivery coordination")
    match = MatchResult(total_score=62, category="exploratory")

    package = generate_materials(job, match, profile, use_llm=False, root=template_project)

    assert "not formal PM ownership" in package.application


def test_language_risk_caveat_and_no_internal_score_in_cv(template_project: Path) -> None:
    profile = load_profile(template_project)
    job = Job(title="SAP ABAP Consultant", description="Mandatory Dutch required")
    match = MatchResult(total_score=55, category="weak", components={"language_risk": -25})

    package = generate_materials(job, match, profile, use_llm=False, root=template_project)

    assert "Language requirements should be confirmed" in package.application
    assert "match score" not in package.cv.lower()


def test_selected_experience_prefers_keyword_relevance(template_project: Path) -> None:
    profile = load_profile(template_project)
    profile["experience"].append(
        {
            "company": "Generic",
            "role": "Other",
            "highlights": ["Generic work."],
            "keywords": ["FI"],
        }
    )
    selected = select_experience(Job(title="SAP QM OData ABAP", description="OData QM ABAP"), profile)

    assert selected[0]["company"] == "LEGO"
