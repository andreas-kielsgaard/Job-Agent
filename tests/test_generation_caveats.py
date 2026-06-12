from __future__ import annotations

from pathlib import Path

from job_agent.config import load_profile
from job_agent.generator import generate_materials, maybe_generate_application_with_llm, select_experience
from job_agent.llm import LlmCompletion
from job_agent.models import Job, MatchResult
from job_agent.scoring import score_job


def test_fiori_role_includes_fiori_caveat(template_project: Path) -> None:
    profile = load_profile(template_project)
    job = Job(title="SAP Fiori Backend Developer", description="Fiori UI5 with ABAP Gateway backend")
    match = score_job(job, profile)

    package = generate_materials(job, match, profile, use_llm=False, root=template_project)

    assert "not a pure UI5 expert" in package.application


def test_project_manager_role_includes_pm_caveat(template_project: Path) -> None:
    profile = load_profile(template_project)
    job = Job(title="SAP Project Manager", description="Project manager with SAP delivery coordination")
    match = score_job(job, profile)

    package = generate_materials(job, match, profile, use_llm=False, root=template_project)

    assert "not formal PM ownership" in package.application


def test_language_risk_caveat_and_no_internal_score_in_cv(template_project: Path) -> None:
    profile = load_profile(template_project)
    job = Job(title="SAP ABAP Consultant", description="Mandatory Dutch required")
    match = MatchResult(total_score=55, category="weak", components={"language_risk": -25})

    package = generate_materials(job, match, profile, use_llm=False, root=template_project)

    assert "Language requirements should be confirmed" in package.application
    assert "Weak match; review gaps manually before applying." in package.match_analysis
    assert "match score" not in package.cv.lower()


def test_blank_available_from_does_not_leave_stray_period(template_project: Path) -> None:
    profile = load_profile(template_project)
    profile["availability"] = {"available_from": "", "logistics": "Can start after agreement."}

    package = generate_materials(
        Job(title="SAP ABAP Consultant", description="ABAP delivery"),
        MatchResult(total_score=80, category="strong"),
        profile,
        use_llm=False,
        root=template_project,
    )

    assert "Availability: . " not in package.application
    assert "Availability: Can start after agreement." in package.application


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


def test_selected_experience_uses_linked_profile_terms(template_project: Path) -> None:
    profile = load_profile(template_project)
    profile["experience"].append(
        {
            "company": "Linked",
            "role": "Relevant",
            "highlights": ["Linked evidence."],
            "keywords": [],
            "linked_skills": ["RAP"],
            "linked_modules": [],
            "linked_roles": [],
        }
    )
    selected = select_experience(Job(title="RAP Developer", description="RAP implementation"), profile)

    assert selected[0]["company"] == "Linked"


def test_application_generation_includes_relevant_human_examples(monkeypatch, template_project: Path) -> None:
    (template_project / "prompts" / "generate_application.md").write_text(
        "Examples:\n{application_examples}\n\nJob: {title}\n{description}",
        encoding="utf-8",
    )
    (template_project / "profile" / "application-examples.yaml").write_text(
        "application_examples:\n"
        "  - id: example-1\n"
        "    label: ABAP note\n"
        "    application_text: Human edited ABAP application phrasing.\n"
        "    linked_skills:\n"
        "      - ABAP\n",
        encoding="utf-8",
    )
    profile = load_profile(template_project)
    prompts = []

    class FakeLlmService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def model_name(self) -> str:
            return "fake-model"

        def is_configured(self) -> bool:
            return True

        def complete(self, prompt: str, **kwargs) -> LlmCompletion:
            prompts.append(prompt)
            return LlmCompletion(text="LLM application text", model="fake-model")

    monkeypatch.setattr("job_agent.generator.LlmService", FakeLlmService)

    package = generate_materials(
        Job(title="SAP ABAP Consultant", description="ABAP delivery"),
        MatchResult(total_score=80, category="strong", matched_keywords=["ABAP"]),
        profile,
        use_llm=True,
        root=template_project,
    )

    assert package.application == "LLM application text\n"
    assert "Human edited ABAP application phrasing." in prompts[0]


def test_application_generation_uses_llm_service(monkeypatch, template_project: Path) -> None:
    profile = load_profile(template_project)
    calls = []

    class FakeLlmService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def model_name(self) -> str:
            return "fake-model"

        def is_configured(self) -> bool:
            return True

        def complete(self, prompt: str, **kwargs) -> LlmCompletion:
            calls.append({"prompt": prompt, **kwargs})
            return LlmCompletion(text="LLM application text", model="fake-model")

    monkeypatch.setattr("job_agent.generator.LlmService", FakeLlmService)

    notes: list[str] = []
    text = maybe_generate_application_with_llm(
        Job(title="SAP ABAP Consultant", description="ABAP"),
        MatchResult(total_score=80, category="strong", recommended_angle="Lead with ABAP"),
        profile,
        selected_experience=[],
        top_skills=["ABAP"],
        generation_notes=notes,
        run_id="run-1",
        stable_id="stable-1",
        root=template_project,
    )

    assert text == "LLM application text\n"
    assert calls[0]["purpose"] == "application_generation"
    assert calls[0]["run_id"] == "run-1"
    assert calls[0]["associated_job_id"] == "stable-1"
    assert any("succeeded with model fake-model" in note for note in notes)


def test_application_generation_falls_back_when_llm_unavailable(monkeypatch, template_project: Path) -> None:
    profile = load_profile(template_project)

    class FakeLlmService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def model_name(self) -> str:
            return "fake-model"

        def is_configured(self) -> bool:
            return False

    monkeypatch.setattr("job_agent.generator.LlmService", FakeLlmService)

    notes: list[str] = []
    text = maybe_generate_application_with_llm(
        Job(title="SAP ABAP Consultant", description="ABAP"),
        MatchResult(total_score=80, category="strong"),
        profile,
        selected_experience=[],
        top_skills=["ABAP"],
        generation_notes=notes,
        root=template_project,
    )

    assert text == ""
    assert any("missing or placeholder" in note for note in notes)
