from __future__ import annotations

from datetime import date

from jinja2 import Environment, FileSystemLoader, select_autoescape

from job_agent.models import Job, JobState, MatchResult


def test_material_templates_render_with_minimal_context(template_project) -> None:
    env = _env(template_project)
    job = Job(title="SAP ABAP Consultant", company="Recruiter", url="https://example.com")
    match = MatchResult(total_score=82, category="strong", recommended_angle="Lead with ABAP")
    context = {
        "job": job,
        "match": match,
        "contact": {
            "name": "Andreas Kielsgaard",
            "title": "SAP Consultant",
            "location": "Denmark",
            "phone": "1",
            "email": "a@example.com",
            "linkedin": "https://linkedin.example",
        },
        "availability": {"available_from": "immediate", "logistics": "Can travel."},
        "location_policy": {"onsite_roles": "Can relocate."},
        "role_summary": "SAP ABAP profile.",
        "top_skills": ["ABAP", "RAP", "CDS", "OData", "Debugging"],
        "selected_experience": [{"company": "LEGO", "role": "SAP ABAP", "relevance": "Built OData."}],
        "keyword_line": "ABAP, RAP",
        "opening": "I am contacting you regarding the role.",
        "caveat_text": "Accurate caveat.",
        "availability_line": "Immediate. Can travel.",
        "material_concerns": [],
        "generation_notes": [],
        "focused_cv": {
            "headline": "SAP Consultant",
            "target": "SAP ABAP Consultant",
            "positioning": "SAP ABAP profile.",
            "skills": [{"name": "ABAP", "detail": "Built OData."}],
            "experience": [{"company": "LEGO", "role": "SAP ABAP", "bullets": ["Built OData."]}],
            "keywords": ["ABAP", "RAP"],
            "caveats": [],
        },
    }

    cv = env.get_template("at-a-glance-cv.md.j2").render(**context)
    focused_cv = env.get_template("focused-cv.md.j2").render(**context)
    focused_cv_html = env.get_template("focused-cv.html.j2").render(**context)
    focused_cv_tex = env.get_template("focused-cv.tex.j2").render(**context)
    application = env.get_template("application-letter.md.j2").render(**context)
    form_answers = env.get_template("form-answers.md.j2").render(
        **context, application_text=application, cv_path="cv.md"
    )
    match_analysis = env.get_template("match-analysis.md.j2").render(**context)

    assert "match score" not in cv.lower()
    assert "Andreas Kielsgaard" in cv
    assert "Focused One-Page CV" in focused_cv
    assert "<!doctype html>" in focused_cv_html
    assert "SAP ABAP Consultant" in focused_cv_html
    assert "\\documentclass" in focused_cv_tex
    assert "Andreas Kielsgaard" in focused_cv_tex
    assert "Standard Form Answer Package" in form_answers
    assert "Score: 82%" in match_analysis


def test_digest_templates_render_with_empty_and_included_items(template_project) -> None:
    env = _env(template_project)
    summary = {
        "total_loaded": 1,
        "new_roles": 1,
        "changed_roles": 0,
        "strong_matches": 1,
        "exploratory_matches": 0,
        "weak_matches": 0,
        "excluded_roles": 0,
        "source_warnings": 0,
    }
    job = Job(title="SAP ABAP Consultant", company="Recruiter", url="https://example.com")
    match = MatchResult(total_score=82, category="strong", reasons=["ABAP"], recommended_angle="Lead with ABAP")
    state = JobState(job=job, stable_id="stable-1", fuzzy_key="fuzzy-1", content_hash="hash", status="new")
    item = {
        "job": job,
        "match": match,
        "state": state,
        "paths": {"cv": "cv.md", "application": "app.md", "form_answers": "forms.md", "match_analysis": "match.md"},
    }

    digest = env.get_template("daily-digest.md.j2").render(
        run_date=date(2026, 5, 6), summary=summary, jobs=[item], source_warnings=[]
    )
    excluded = env.get_template("excluded-summary.md.j2").render(
        run_date=date(2026, 5, 6), excluded=[], source_warnings=[]
    )

    assert "SAP ABAP Consultant" in digest
    assert "No roles were filtered out." in excluded


def _env(root):
    env = Environment(
        loader=FileSystemLoader(root / "templates"),
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["latex"] = _latex_escape
    return env


def _latex_escape(value) -> str:
    escapes = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(escapes.get(char, char) for char in str(value))
