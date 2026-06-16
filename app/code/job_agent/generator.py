from __future__ import annotations

import json
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
    llm_model: str = "",
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
            llm_model=llm_model,
        )
    else:
        generation_notes.append("Claude disabled; deterministic application template used.")

    env = Environment(
        loader=FileSystemLoader(templates_dir(root)),
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["latex"] = _latex_escape
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
    focused_cv_model = build_focused_cv_model(
        job,
        match,
        profile,
        selected_experience,
        top_skills,
        role_summary,
        material_concerns,
    )
    if use_llm:
        llm_focused_cv = maybe_generate_focused_cv_with_llm(
            job,
            match,
            profile,
            selected_experience,
            top_skills,
            focused_cv_model,
            generation_notes,
            run_id=run_id,
            stable_id=stable_id,
            root=root,
            progress_callback=progress_callback,
            llm_model=llm_model,
        )
        if llm_focused_cv:
            focused_cv_model = llm_focused_cv
    context["focused_cv"] = focused_cv_model

    cv = env.get_template("at-a-glance-cv.md.j2").render(**context).strip() + "\n"
    focused_cv = env.get_template("focused-cv.md.j2").render(**context).strip() + "\n"
    focused_cv_html = env.get_template("focused-cv.html.j2").render(**context).strip() + "\n"
    focused_cv_tex = env.get_template("focused-cv.tex.j2").render(**context).strip() + "\n"
    focused_cv_pdf = _build_focused_cv_pdf(focused_cv_model, profile.get("contact", {}))
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
        focused_cv=focused_cv,
        focused_cv_html=focused_cv_html,
        focused_cv_tex=focused_cv_tex,
        focused_cv_pdf=focused_cv_pdf,
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


def build_focused_cv_model(
    job: Job,
    match: MatchResult,
    profile: dict,
    selected_experience: list[dict[str, Any]],
    top_skills: list[str],
    role_summary: str,
    material_concerns: list[str],
) -> dict[str, Any]:
    contact = profile.get("contact", {})
    title = str(contact.get("title") or "SAP Consultant").strip()
    keywords = _dedupe(match.matched_keywords + top_skills)[:8]
    return {
        "headline": title,
        "target": job.title,
        "positioning": _clip(role_summary, 230),
        "skills": [{"name": skill, "detail": _skill_detail(skill, selected_experience)} for skill in top_skills[:6]],
        "experience": [
            {
                "company": str(item.get("company") or "").strip(),
                "role": str(item.get("role") or "").strip(),
                "bullets": _experience_bullets(str(item.get("relevance") or "")),
            }
            for item in selected_experience[:2]
        ],
        "keywords": keywords,
        "caveats": _dedupe(material_concerns)[:2],
    }


def maybe_generate_focused_cv_with_llm(
    job: Job,
    match: MatchResult,
    profile: dict,
    selected_experience: list[dict[str, Any]],
    top_skills: list[str],
    fallback_model: dict[str, Any],
    generation_notes: list[str],
    run_id: str = "",
    stable_id: str = "",
    root: Path = ROOT,
    progress_callback=None,
    llm_model: str = "",
) -> dict[str, Any] | None:
    llm = LlmService(root)
    model = llm_model or llm.model_name()
    if not llm.is_configured():
        generation_notes.append("Focused one-page CV used deterministic fallback because Claude is not configured.")
        return None
    try:
        _emit(
            progress_callback,
            run_id,
            "focused_cv_started",
            f"Claude focused CV generation started with model {model}.",
            "generation",
            job.title,
        )
        completion = llm.complete(
            build_focused_cv_llm_prompt(job, match, profile, selected_experience, top_skills, fallback_model, root),
            max_tokens=1300,
            purpose="focused_cv_generation",
            run_id=run_id,
            associated_job_id=stable_id,
            model=llm_model,
        )
        data = _parse_json_object(completion.text)
        focused = _normalize_focused_cv_model(data, fallback_model, top_skills)
        generation_notes.append(f"Claude focused CV generation succeeded with model {completion.model}.")
        _emit(
            progress_callback,
            run_id,
            "focused_cv_completed",
            f"Claude focused CV generation completed with model {completion.model}.",
            "generation",
            job.title,
        )
        return focused
    except Exception as exc:
        generation_notes.append(
            f"Claude focused CV generation failed with model {model}: {exc}. Deterministic fallback used."
        )
        _emit(
            progress_callback,
            run_id,
            "focused_cv_failed",
            f"Claude focused CV generation failed with model {model}: {exc}.",
            "generation",
            job.title,
        )
        return None


def build_focused_cv_llm_prompt(
    job: Job,
    match: MatchResult,
    profile: dict,
    selected_experience: list[dict[str, Any]],
    top_skills: list[str],
    fallback_model: dict[str, Any],
    root: Path = ROOT,
) -> str:
    return _load_prompt("generate_focused_cv.md", root).format(
        canonical_cv=profile.get("canonical_cv", ""),
        writing_style=profile.get("writing_style", ""),
        contact_title=profile.get("contact", {}).get("title", ""),
        top_skills=top_skills,
        selected_experience=selected_experience,
        fallback_json=json.dumps(fallback_model, ensure_ascii=False, indent=2),
        title=job.title,
        company=job.company,
        location=job.location,
        remote=job.remote,
        description=job.description,
        concerns=match.concerns,
        recommended_angle=match.recommended_angle,
    )


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
    llm_model: str = "",
) -> str:
    llm = LlmService(root)
    model = llm_model or llm.model_name()
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
            model=llm_model,
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


def _build_focused_cv_pdf(focused_cv: dict[str, Any], contact: dict[str, Any]) -> bytes:
    width = 595.28
    height = 841.89
    margin = 42.0
    y = height - 48.0
    ops: list[str] = []

    _pdf_rect(ops, margin, height - 36.0, width - (margin * 2), 4.5, (0.17, 0.43, 0.36))
    _pdf_text(ops, str(contact.get("name") or "Focused CV"), margin, y, 22, bold=True)
    y -= 21
    _pdf_text(ops, str(focused_cv.get("headline") or ""), margin, y, 11, color=(0.17, 0.43, 0.36))
    y -= 16

    contact_bits = [
        str(contact.get("location") or "").strip(),
        str(contact.get("email") or "").strip(),
        str(contact.get("phone") or "").strip(),
        str(contact.get("linkedin") or "").strip(),
    ]
    _pdf_text(ops, " | ".join(bit for bit in contact_bits if bit), margin, y, 8.8, color=(0.25, 0.31, 0.29))
    y -= 24

    y = _pdf_section(ops, "Target Focus", y, margin, width)
    y = _pdf_wrapped(ops, str(focused_cv.get("target") or ""), margin, y, width - (margin * 2), 9.5, max_lines=2)
    y -= 8

    y = _pdf_section(ops, "Recruiter Profile", y, margin, width)
    y = _pdf_wrapped(
        ops,
        str(focused_cv.get("positioning") or ""),
        margin,
        y,
        width - (margin * 2),
        9.5,
        max_lines=4,
    )
    y -= 8

    y = _pdf_section(ops, "Selected Skills", y, margin, width)
    for item in _list(focused_cv.get("skills"))[:6]:
        if not isinstance(item, dict):
            continue
        line = f"{item.get('name')}: {item.get('detail')}"
        y = _pdf_wrapped(ops, line, margin, y, width - (margin * 2), 8.8, max_lines=2, bullet=True)
        if y < margin + 90:
            break
    y -= 7

    y = _pdf_section(ops, "Selected Experience", y, margin, width)
    for item in _list(focused_cv.get("experience"))[:2]:
        if not isinstance(item, dict) or y < margin + 64:
            continue
        heading = " - ".join(
            part for part in [str(item.get("company") or "").strip(), str(item.get("role") or "").strip()] if part
        )
        _pdf_text(ops, heading, margin, y, 9.3, bold=True)
        y -= 13
        for bullet in _list(item.get("bullets"))[:2]:
            y = _pdf_wrapped(ops, str(bullet), margin, y, width - (margin * 2), 8.6, max_lines=2, bullet=True)
        y -= 5

    keywords = [str(keyword) for keyword in _list(focused_cv.get("keywords")) if str(keyword).strip()]
    if keywords and y >= margin + 58:
        y = _pdf_section(ops, "Keywords", y, margin, width)
        y = _pdf_wrapped(ops, ", ".join(keywords[:8]), margin, y, width - (margin * 2), 8.5, max_lines=3)
        y -= 6

    caveats = [str(caveat) for caveat in _list(focused_cv.get("caveats")) if str(caveat).strip()]
    if caveats and y >= margin + 50:
        y = _pdf_section(ops, "Review Notes", y, margin, width)
        for caveat in caveats[:2]:
            y = _pdf_wrapped(ops, caveat, margin, y, width - (margin * 2), 8.1, max_lines=1, bullet=True)

    return _pdf_document("\n".join(ops) + "\n")


def _pdf_section(ops: list[str], title: str, y: float, margin: float, width: float) -> float:
    y -= 2
    _pdf_text(ops, title.upper(), margin, y, 8.2, bold=True, color=(0.09, 0.13, 0.12))
    _pdf_rect(ops, margin, y - 4.5, width - (margin * 2), 0.7, (0.78, 0.84, 0.82))
    return y - 15


def _pdf_wrapped(
    ops: list[str],
    text: str,
    x: float,
    y: float,
    max_width: float,
    size: float,
    max_lines: int,
    bullet: bool = False,
) -> float:
    prefix = "- " if bullet else ""
    indent = 10.0 if bullet else 0.0
    lines = _pdf_wrap_text(text, max_width - indent, size)[:max_lines]
    for index, line in enumerate(lines):
        if y < 48:
            return y
        _pdf_text(ops, f"{prefix if index == 0 else '  '}{line}", x + indent, y, size, color=(0.16, 0.21, 0.19))
        y -= size + 3.2
    return y


def _pdf_wrap_text(text: str, max_width: float, size: float) -> list[str]:
    max_chars = max(18, int(max_width / (size * 0.48)))
    words = " ".join(text.split()).split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(word) > max_chars:
            if current:
                lines.append(current)
                current = ""
            lines.extend(word[start : start + max_chars] for start in range(0, len(word), max_chars))
            continue
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _pdf_text(
    ops: list[str],
    text: str,
    x: float,
    y: float,
    size: float,
    bold: bool = False,
    color: tuple[float, float, float] = (0.08, 0.11, 0.1),
) -> None:
    font = "F2" if bold else "F1"
    red, green, blue = color
    ops.append("BT")
    ops.append(f"{red:.3f} {green:.3f} {blue:.3f} rg")
    ops.append(f"/{font} {size:.2f} Tf")
    ops.append(f"1 0 0 1 {x:.2f} {y:.2f} Tm")
    ops.append(f"({_pdf_escape(text)}) Tj")
    ops.append("ET")


def _pdf_rect(
    ops: list[str],
    x: float,
    y: float,
    width: float,
    height: float,
    color: tuple[float, float, float],
) -> None:
    red, green, blue = color
    ops.append(f"{red:.3f} {green:.3f} {blue:.3f} rg {x:.2f} {y:.2f} {width:.2f} {height:.2f} re f")


def _pdf_escape(text: str) -> str:
    safe = str(text).encode("latin-1", errors="replace").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_document(content: str) -> bytes:
    stream = content.encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] "
            b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
    ]
    parts = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(part) for part in parts))
        parts.append(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref_offset = sum(len(part) for part in parts)
    parts.append(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets:
        parts.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    parts.append(
        (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").encode("ascii")
    )
    return b"".join(parts)


_LATEX_ESCAPES = {
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


def _latex_escape(value: Any) -> str:
    return "".join(_LATEX_ESCAPES.get(char, char) for char in str(value))


def _load_prompt(name: str, root: Path = ROOT) -> str:
    path = prompts_dir(root) / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    if name == "generate_focused_cv.md":
        return (
            "Return only valid JSON for a one-page recruiter CV. Use only supplied evidence. "
            "Do not invent facts, employers, dates, certifications, tools, locations, or metrics.\n\n"
            "This JSON is rendered into a LaTeX one-page CV and PDF preview. "
            "Style for the LaTeX/PDF output: compact A4 page, clear hierarchy, restrained accent color, "
            "short evidence-led sections, no decorative claims.\n\n"
            "Fallback JSON:\n{fallback_json}\n\nCanonical CV:\n{canonical_cv}\n\n"
            "Top selected skills: {top_skills}\nSelected experience: {selected_experience}\nJob: {title}\n{description}"
        )
    return "{canonical_cv}\n\nWrite application for {title}."


def _normalize_focused_cv_model(
    data: dict[str, Any],
    fallback: dict[str, Any],
    allowed_skills: list[str],
) -> dict[str, Any]:
    allowed_skill_lookup = {skill.lower(): skill for skill in allowed_skills}
    fallback_experience = [item for item in fallback.get("experience", []) if isinstance(item, dict)]
    experience_by_key = {
        (str(item.get("company") or "").lower(), str(item.get("role") or "").lower()): item
        for item in fallback_experience
    }
    skills = []
    for item in _list(data.get("skills")):
        if not isinstance(item, dict):
            continue
        name = allowed_skill_lookup.get(str(item.get("name") or "").lower())
        if not name:
            continue
        skills.append({"name": name, "detail": _clip(str(item.get("detail") or ""), 95)})
        if len(skills) >= 6:
            break
    experience = []
    for item in _list(data.get("experience")):
        if not isinstance(item, dict):
            continue
        key = (str(item.get("company") or "").lower(), str(item.get("role") or "").lower())
        fallback_item = experience_by_key.get(key)
        if not fallback_item:
            continue
        bullets = [_clip(str(bullet), 120) for bullet in _list(item.get("bullets")) if str(bullet).strip()]
        experience.append({**fallback_item, "bullets": bullets[:2] or fallback_item.get("bullets", [])})
        if len(experience) >= 2:
            break
    keywords = [
        keyword
        for keyword in _dedupe([str(item).strip() for item in _list(data.get("keywords"))])
        if keyword in fallback.get("keywords", [])
    ][:8]
    return {
        **fallback,
        "headline": _clip(str(data.get("headline") or fallback.get("headline") or ""), 95),
        "target": _clip(str(data.get("target") or fallback.get("target") or ""), 95),
        "positioning": _clip(str(data.get("positioning") or fallback.get("positioning") or ""), 230),
        "skills": skills or fallback.get("skills", []),
        "experience": experience or fallback_experience,
        "keywords": keywords or fallback.get("keywords", []),
        "caveats": fallback.get("caveats", []),
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM response did not contain a JSON object.")
    data = json.loads(stripped[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM response JSON was not an object.")
    return data


def _skill_detail(skill: str, selected_experience: list[dict[str, Any]]) -> str:
    for item in selected_experience:
        relevance = str(item.get("relevance") or "")
        if skill.lower() in relevance.lower():
            return _clip(relevance, 95)
    return "Selected because it appears in the role/profile overlap."


def _experience_bullets(relevance: str) -> list[str]:
    parts = [part.strip() for part in relevance.replace("\n", " ").split(".") if part.strip()]
    return [_sentence(_clip(part, 120)) for part in parts[:2]] or [
        "Relevant delivery experience selected from the master CV."
    ]


def _clip(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "."


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
