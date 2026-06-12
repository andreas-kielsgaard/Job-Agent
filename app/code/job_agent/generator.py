from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .llm import LlmService
from .models import GeneratedPackage, Job, MatchResult
from .paths import prompts_dir, templates_dir
from .run_store import RunEvent
from .services.application_examples_service import ApplicationExamplesService, format_examples_for_prompt


def generate_materials(
    job: Job,
    match: MatchResult,
    profile: dict,
    use_llm: bool = False,
    root: Path = ROOT,
    run_id: str = "",
    stable_id: str = "",
    progress_callback=None,
    application_override: str = "",
) -> GeneratedPackage:
    selected_experience = select_experience(job, profile)
    top_skills = select_skills(job, match, profile)
    application_examples = ApplicationExamplesService(root).select_relevant(job, match, profile)
    role_summary = build_role_summary(job, top_skills, profile)
    caveat_text = build_caveat_text(job, match, profile)
    application_opening = build_application_opening(job, match)
    material_concerns = build_material_concerns(match)
    availability_line = build_availability_line(profile.get("availability", {}))
    generation_notes: list[str] = []

    llm_application = ""
    if application_override.strip():
        generation_notes.append(
            "Application text supplied from external agent; deterministic supporting materials used."
        )
    elif use_llm:
        llm_application = maybe_generate_application_with_llm(
            job,
            match,
            profile,
            selected_experience,
            top_skills,
            generation_notes,
            application_examples,
            run_id=run_id,
            stable_id=stable_id,
            root=root,
            progress_callback=progress_callback,
        )
    else:
        generation_notes.append("Claude disabled; deterministic application template used.")

    env = Environment(
        loader=FileSystemLoader(templates_dir(root)),
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
        "application_examples": application_examples,
        "keyword_line": ", ".join(_dedupe(match.matched_keywords + top_skills)),
        "opening": application_opening,
        "caveat_text": caveat_text,
        "material_concerns": material_concerns,
        "availability_line": availability_line,
        "generation_notes": generation_notes,
    }

    cv = env.get_template("at-a-glance-cv.md.j2").render(**context).strip() + "\n"
    application = (
        application_override.strip() + "\n"
        if application_override.strip()
        else llm_application or env.get_template("application-letter.md.j2").render(**context).strip() + "\n"
    )
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
        keywords = _experience_terms(item)
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


def build_role_summary(job: Job, top_skills: list[str], profile: dict) -> str:
    onsite = ""
    if any(term in f"{job.remote} {job.location}".lower() for term in ["onsite", "hybrid"]):
        onsite = " Includes availability for hybrid or onsite setup where logistics are workable."
    profile_title = profile.get("contact", {}).get("title") or "Candidate profile"
    skill_focus = ", ".join(top_skills[:3]) or "the most relevant verified skills"
    return (
        f"{profile_title} aligned with {job.title}, with emphasis on {skill_focus}. "
        f"Background combines relevant delivery experience, practical problem solving, and clear communication "
        f"around scope and partial matches.{onsite}"
    )


def build_application_opening(job: Job, match: MatchResult) -> str:
    return (
        f"I am contacting you regarding the {job.title} role. The strongest overlap is "
        f"{', '.join(match.matched_keywords[:6]) or 'the configured profile'}, supported by relevant project delivery."
    )


def build_caveat_text(job: Job, match: MatchResult, profile: dict) -> str:
    caveats = [
        concern
        for concern in match.concerns
        if concern
        and not concern.startswith(
            (
                "Freshness is uncertain",
                "Rate or salary is not listed",
                "Permanent employment conflicts",
                "Remote or hybrid setup",
                "Missing required match rule",
                "Extraction confidence is low",
            )
        )
    ]
    if match.components.get("language_risk", 0) < 0:
        caveats.append("Language requirements should be confirmed before applying.")
    if match.category == "weak":
        caveats.append("This is a weak match; review the gaps manually before using this draft.")
    if caveats:
        return " ".join(_dedupe(caveats))
    return "The application keeps the focus on the parts of the role where the background is strongest."


def build_material_concerns(match: MatchResult) -> list[str]:
    concerns = list(match.concerns)
    if match.category == "weak":
        concerns.append("Weak match; review gaps manually before applying.")
    return _dedupe(concerns)


def build_availability_line(availability: dict[str, Any]) -> str:
    available_from = str(availability.get("available_from") or "").strip()
    logistics = str(availability.get("logistics") or "").strip()
    parts = [_sentence(available_from), _sentence(logistics)]
    line = " ".join(part for part in parts if part)
    return line or "Manual confirmation recommended."


def maybe_generate_application_with_llm(
    job: Job,
    match: MatchResult,
    profile: dict,
    selected_experience: list[dict[str, str]],
    top_skills: list[str],
    generation_notes: list[str],
    application_examples: list[dict[str, Any]] | None = None,
    run_id: str = "",
    stable_id: str = "",
    root: Path = ROOT,
    progress_callback=None,
) -> str:
    llm = LlmService(root)
    model = llm.model_name()
    if not llm.is_configured():
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
        _emit(
            progress_callback,
            run_id,
            "claude_started",
            f"Claude application generation started with model {model}.",
            "generation",
            job.title,
        )
        prompt = build_application_llm_prompt(
            job,
            match,
            profile,
            selected_experience,
            top_skills,
            application_examples or [],
            root=root,
        )
        completion = llm.complete(
            prompt,
            max_tokens=700,
            purpose="application_generation",
            run_id=run_id,
            associated_job_id=stable_id,
        )
        generation_notes.append(f"Claude application generation succeeded with model {completion.model}.")
        _emit(
            progress_callback,
            run_id,
            "claude_completed",
            f"Claude application generation completed with model {completion.model}.",
            "generation",
            job.title,
        )
        return completion.text.strip() + "\n"
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


def build_application_llm_prompt(
    job: Job,
    match: MatchResult,
    profile: dict,
    selected_experience: list[dict[str, str]],
    top_skills: list[str],
    application_examples: list[dict[str, Any]] | None = None,
    root: Path = ROOT,
) -> str:
    return _load_prompt("generate_application.md", root).format(
        canonical_cv=profile.get("canonical_cv", ""),
        writing_style=profile.get("writing_style", ""),
        top_skills=", ".join(top_skills),
        selected_experience=selected_experience,
        application_examples=format_examples_for_prompt(application_examples or []),
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


def _load_prompt(name: str, root: Path = ROOT) -> str:
    path = prompts_dir(root) / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "{canonical_cv}\n\nWrite application for {title}."


def _experience_terms(item: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ["keywords", "linked_skills", "linked_modules", "linked_roles"]:
        value = item.get(key, [])
        if isinstance(value, list):
            terms.extend(str(part) for part in value if str(part).strip())
        elif str(value).strip():
            terms.append(str(value))
    return _dedupe([term.strip() for term in terms if term.strip()])


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _sentence(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    return text if text[-1:] in {".", "!", "?"} else f"{text}."


def _emit(progress_callback, run_id: str, event_type: str, message: str, phase: str, current_job: str) -> None:
    if progress_callback and run_id:
        progress_callback(
            RunEvent(run_id=run_id, event_type=event_type, message=message, phase=phase, current_job=current_job)
        )
