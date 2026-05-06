from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .models import GeneratedPackage, Job, MatchResult
from .run_store import RunEvent
from .token_usage import TokenUsageStore, token_record_from_anthropic_response


def generate_materials(
    job: Job,
    match: MatchResult,
    profile: dict,
    use_llm: bool = False,
    root: Path = ROOT,
    run_id: str = "",
    stable_id: str = "",
    progress_callback=None,
) -> GeneratedPackage:
    selected_experience = select_experience(job, profile)
    top_skills = select_skills(job, match, profile)
    role_summary = build_role_summary(job, top_skills)
    caveat_text = build_caveat_text(job, match, profile)
    application_opening = build_application_opening(job, match)
    generation_notes: list[str] = []

    llm_application = ""
    if use_llm:
        llm_application = maybe_generate_application_with_llm(
            job,
            match,
            profile,
            selected_experience,
            top_skills,
            generation_notes,
            run_id=run_id,
            stable_id=stable_id,
            root=root,
            progress_callback=progress_callback,
        )
    else:
        generation_notes.append("Claude disabled; deterministic application template used.")

    env = Environment(
        loader=FileSystemLoader(root / "templates"),
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    context = {
        "job": job,
        "match": match,
        "contact": profile["contact"],
        "availability": profile["availability"],
        "location_policy": profile["location_policy"],
        "role_summary": role_summary,
        "top_skills": top_skills,
        "selected_experience": selected_experience,
        "keyword_line": ", ".join(_dedupe(match.matched_keywords + top_skills)),
        "opening": application_opening,
        "caveat_text": caveat_text,
        "generation_notes": generation_notes,
    }

    cv = env.get_template("at-a-glance-cv.md.j2").render(**context).strip() + "\n"
    application = llm_application or env.get_template("application-letter.md.j2").render(**context).strip() + "\n"
    form_answers = (
        env.get_template("form-answers.md.j2")
        .render(
            **context,
            application_text=application,
            cv_path="[generated alongside this form-answer file]",
        )
        .strip()
        + "\n"
    )
    match_analysis = env.get_template("match-analysis.md.j2").render(**context).strip() + "\n"

    return GeneratedPackage(
        cv=cv,
        application=application,
        form_answers=form_answers,
        match_analysis=match_analysis,
        selected_experience=selected_experience,
        top_skills=top_skills,
        generation_notes=generation_notes,
    )


def select_skills(job: Job, match: MatchResult, profile: dict) -> list[str]:
    text = f"{job.title} {job.description} {' '.join(job.required_skills)}".lower()
    all_skills = profile.get("skills", {}).get("strongest", [])
    ranked = sorted(all_skills, key=lambda skill: (skill.lower() not in text, all_skills.index(skill)))
    return ranked[:5]


def select_experience(job: Job, profile: dict) -> list[dict[str, str]]:
    text = f"{job.title} {job.description} {' '.join(job.required_skills)} {' '.join(job.required_modules)}".lower()
    scored = []
    for index, item in enumerate(profile.get("experience", [])):
        keywords = item.get("keywords", [])
        score = sum(1 for keyword in keywords if keyword.lower() in text)
        scored.append((score, -index, item))
    selected = [item for _, _, item in sorted(scored, reverse=True)[:2]]
    result = []
    for item in selected:
        highlights = item.get("highlights", [])
        result.append(
            {
                "company": item["company"],
                "role": item["role"],
                "relevance": " ".join(highlights[:2]),
            }
        )
    return result


def build_role_summary(job: Job, top_skills: list[str]) -> str:
    onsite = ""
    if any(term in f"{job.remote} {job.location}".lower() for term in ["onsite", "hybrid"]):
        onsite = " Includes availability for hybrid or onsite setup where logistics are workable."
    return (
        f"SAP technical consultant profile aligned with {job.title}, with emphasis on "
        f"{', '.join(top_skills[:3])}. Background combines hands-on delivery, debugging, integration work, "
        f"and clear communication around scope and partial matches.{onsite}"
    )


def build_application_opening(job: Job, match: MatchResult) -> str:
    return (
        f"I am contacting you regarding the {job.title} role. The strongest overlap is "
        f"{', '.join(match.matched_keywords[:6]) or 'SAP technical consulting'}, supported by concrete SAP project delivery."
    )


def build_caveat_text(job: Job, match: MatchResult, profile: dict) -> str:
    caveats = []
    text = f"{job.title} {job.description}".lower()
    if "fiori" in text or "ui5" in text:
        caveats.append(profile.get("skills", {}).get("caveats", {}).get("fiori", "Clarify Fiori/UI5 depth."))
    if "project manager" in text or "transition manager" in text or "service delivery manager" in text:
        caveats.append(
            profile.get("skills", {})
            .get("caveats", {})
            .get("project_management", "Clarify project management ownership depth.")
        )
    if match.components.get("language_risk", 0) < 0:
        caveats.append("Language requirements should be confirmed before applying.")
    if caveats:
        return " ".join(caveats)
    return "The application keeps the focus on the parts of the role where the background is strongest."


def maybe_generate_application_with_llm(
    job: Job,
    match: MatchResult,
    profile: dict,
    selected_experience: list[dict[str, str]],
    top_skills: list[str],
    generation_notes: list[str],
    run_id: str = "",
    stable_id: str = "",
    root: Path = ROOT,
    progress_callback=None,
) -> str:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-0")
    if not api_key or api_key.startswith("replace_with"):
        generation_notes.append(
            "Claude requested but ANTHROPIC_API_KEY is missing or placeholder; deterministic fallback used."
        )
        _emit(
            progress_callback,
            run_id,
            "claude_skipped",
            "Claude key missing or placeholder; deterministic fallback used.",
            "generation",
            job.title,
        )
        return ""

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)
        _emit(
            progress_callback,
            run_id,
            "claude_started",
            f"Claude application generation started with model {model}.",
            "generation",
            job.title,
        )
        prompt = _load_prompt("generate_application.md").format(
            canonical_cv=profile.get("canonical_cv", ""),
            writing_style=profile.get("writing_style", ""),
            top_skills=", ".join(top_skills),
            selected_experience=selected_experience,
            title=job.title,
            company=job.company,
            location=job.location,
            remote=job.remote,
            description=job.description,
            concerns=match.concerns,
            recommended_angle=match.recommended_angle,
            availability=profile.get("availability", {}),
            location_policy=profile.get("location_policy", {}),
        )
        response = client.messages.create(
            model=model,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        if run_id:
            record = token_record_from_anthropic_response(
                run_id=run_id,
                purpose="application_generation",
                model=model,
                associated_job_id=stable_id,
                response=response,
            )
            TokenUsageStore(root).append(record)
        generation_notes.append(f"Claude application generation succeeded with model {model}.")
        _emit(
            progress_callback,
            run_id,
            "claude_completed",
            f"Claude application generation completed with model {model}.",
            "generation",
            job.title,
        )
        return "".join(block.text for block in response.content if block.type == "text").strip() + "\n"
    except Exception as exc:
        generation_notes.append(
            f"Claude application generation failed with model {model}: {exc}. Deterministic fallback used."
        )
        _emit(
            progress_callback,
            run_id,
            "claude_failed",
            f"Claude application generation failed with model {model}: {exc}.",
            "generation",
            job.title,
        )
        return ""


def _load_prompt(name: str) -> str:
    path = ROOT / "prompts" / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "{canonical_cv}\n\nWrite application for {title}."


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _emit(progress_callback, run_id: str, event_type: str, message: str, phase: str, current_job: str) -> None:
    if progress_callback and run_id:
        progress_callback(
            RunEvent(run_id=run_id, event_type=event_type, message=message, phase=phase, current_job=current_job)
        )
