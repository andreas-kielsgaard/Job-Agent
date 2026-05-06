from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .models import GeneratedPackage, Job, MatchResult


def generate_materials(job: Job, match: MatchResult, profile: dict, use_llm: bool = False, root: Path = ROOT) -> GeneratedPackage:
    selected_experience = select_experience(job, profile)
    top_skills = select_skills(job, match, profile)
    role_summary = build_role_summary(job, match, top_skills)
    caveat_text = build_caveat_text(job, match)
    application_opening = build_application_opening(job, match)

    llm_application = maybe_generate_application_with_llm(job, match, profile, selected_experience, top_skills) if use_llm else ""
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
        "keyword_line": ", ".join(match.matched_keywords + top_skills),
        "opening": application_opening,
        "caveat_text": caveat_text,
    }

    cv = env.get_template("at-a-glance-cv.md.j2").render(**context).strip() + "\n"
    application = llm_application or env.get_template("application-letter.md.j2").render(**context).strip() + "\n"
    form_answers = env.get_template("form-answers.md.j2").render(
        **context,
        application_text=application,
        cv_path="[generated alongside this form-answer file]",
    ).strip() + "\n"

    return GeneratedPackage(cv=cv, application=application, form_answers=form_answers, selected_experience=selected_experience, top_skills=top_skills)


def select_skills(job: Job, match: MatchResult, profile: dict) -> list[str]:
    text = f"{job.title} {job.description}".lower()
    all_skills = profile.get("skills", {}).get("strongest", [])
    ranked = sorted(all_skills, key=lambda skill: (skill.lower() not in text, all_skills.index(skill)))
    return ranked[:5]


def select_experience(job: Job, profile: dict) -> list[dict[str, str]]:
    text = f"{job.title} {job.description}".lower()
    scored = []
    for item in profile.get("experience", []):
        keywords = item.get("keywords", [])
        score = sum(1 for keyword in keywords if keyword.lower() in text)
        scored.append((score, item))
    selected = [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:2]]
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


def build_role_summary(job: Job, match: MatchResult, top_skills: list[str]) -> str:
    return (
        f"Relevant SAP consultant profile for {job.title}, emphasizing "
        f"{', '.join(top_skills[:3])}. Match score is {match.score}%, with the strongest signals coming from "
        f"{', '.join(match.matched_keywords[:6]) or 'the role description'}."
    )


def build_application_opening(job: Job, match: MatchResult) -> str:
    return (
        f"I am interested in the {job.title} role. Based on the description, the strongest overlap is "
        f"{', '.join(match.matched_keywords[:6]) or 'SAP technical consulting'}, supported by concrete SAP delivery experience."
    )


def build_caveat_text(job: Job, match: MatchResult) -> str:
    if match.concerns:
        return "Points to clarify: " + " ".join(match.concerns)
    return "I have kept the application focused on the parts of the role where my background is strongest."


def maybe_generate_application_with_llm(
    job: Job,
    match: MatchResult,
    profile: dict,
    selected_experience: list[dict[str, str]],
    top_skills: list[str],
) -> str:
    load_dotenv()
    if not os.getenv("ANTHROPIC_API_KEY"):
        return ""

    try:
        from anthropic import Anthropic

        client = Anthropic()
        model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        prompt = f"""
Write a concise application for this SAP contract role.

Rules:
- Use direct consultant tone.
- Do not use "passionate", "excited", "dynamic", or "perfect fit".
- Do not exaggerate skills.
- Explicitly mention partial matches when relevant.
- Emphasize relocation policy if onsite work is required.
- Mention immediate availability with logistics caveat.
- Prefer concrete project references over generic traits.

Profile:
{profile.get("canonical_cv", "")}

Writing style:
{profile.get("writing_style", "")}

Top selected skills:
{", ".join(top_skills)}

Selected experience:
{selected_experience}

Job:
Title: {job.title}
Company: {job.company}
Location: {job.location}
Remote: {job.remote}
Description:
{job.description}

Match concerns:
{match.concerns}
"""
        response = client.messages.create(
            model=model,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text").strip() + "\n"
    except Exception:
        return ""
