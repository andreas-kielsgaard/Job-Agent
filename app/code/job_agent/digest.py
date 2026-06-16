from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .io.atomic import atomic_write_bytes, atomic_write_text
from .io.json_store import write_json
from .models import GeneratedPackage, Job, MatchResult, SourceWarning
from .paths import output_dir, templates_dir
from .store import JobStore


def write_job_package(
    job: Job,
    match: MatchResult,
    package: GeneratedPackage,
    run_date: date,
    root: Path = ROOT,
    run_id: str = "",
    stable_id: str = "",
    fuzzy_key: str = "",
    state: str = "",
    application_status: str = "unreviewed",
    ai_evaluation: dict | None = None,
    review_list: bool = True,
) -> dict[str, str]:
    slug = slugify(job.title)
    base = output_dir(root) / str(run_date) / slug
    base.mkdir(parents=True, exist_ok=True)

    paths = {
        "job": base / "job.json",
        "posting_snapshot": base / "posting-snapshot.md",
        "cv": base / "cv-at-a-glance.md",
        "focused_cv": base / "cv-one-page.md",
        "focused_cv_html": base / "cv-one-page.html",
        "focused_cv_tex": base / "cv-one-page.tex",
        "focused_cv_pdf": base / "cv-one-page.pdf",
        "application": base / "application.md",
        "form_answers": base / "form-answers.md",
        "match_analysis": base / "match-analysis.md",
        "match": base / "match.json",
        "index": base / "index.json",
    }

    write_json(paths["job"], asdict(job))
    atomic_write_text(paths["posting_snapshot"], _posting_snapshot(job, match, run_date), encoding="utf-8")
    write_json(paths["match"], asdict(match))
    atomic_write_text(paths["cv"], package.cv, encoding="utf-8")
    atomic_write_text(paths["focused_cv"], package.focused_cv, encoding="utf-8")
    atomic_write_text(paths["focused_cv_html"], package.focused_cv_html, encoding="utf-8")
    atomic_write_text(paths["focused_cv_tex"], package.focused_cv_tex, encoding="utf-8")
    if package.focused_cv_pdf:
        atomic_write_bytes(paths["focused_cv_pdf"], package.focused_cv_pdf)
    else:
        paths["focused_cv_pdf"].unlink(missing_ok=True)
    atomic_write_text(paths["application"], package.application, encoding="utf-8")
    atomic_write_text(paths["match_analysis"], package.match_analysis, encoding="utf-8")
    atomic_write_text(
        paths["form_answers"],
        package.form_answers.replace("[generated alongside this form-answer file]", str(paths["focused_cv_pdf"])),
        encoding="utf-8",
    )
    write_json(
        paths["index"],
        _package_index(
            job,
            match,
            slug,
            paths,
            run_id,
            stable_id,
            fuzzy_key,
            state,
            application_status,
            True,
            ai_evaluation,
            review_list,
        ),
    )

    return {name: str(path) for name, path in paths.items()}


def write_placeholder_job_package(
    job: Job,
    match: MatchResult,
    run_date: date,
    root: Path = ROOT,
    run_id: str = "",
    stable_id: str = "",
    fuzzy_key: str = "",
    state: str = "",
    application_status: str = "unreviewed",
    ai_evaluation: dict | None = None,
    review_list: bool = True,
) -> dict[str, str]:
    slug = slugify(job.title)
    base = output_dir(root) / str(run_date) / slug
    base.mkdir(parents=True, exist_ok=True)

    paths = {
        "job": base / "job.json",
        "posting_snapshot": base / "posting-snapshot.md",
        "cv": base / "cv-at-a-glance.md",
        "focused_cv": base / "cv-one-page.md",
        "focused_cv_html": base / "cv-one-page.html",
        "focused_cv_tex": base / "cv-one-page.tex",
        "focused_cv_pdf": base / "cv-one-page.pdf",
        "application": base / "application.md",
        "form_answers": base / "form-answers.md",
        "match_analysis": base / "match-analysis.md",
        "match": base / "match.json",
        "index": base / "index.json",
    }
    write_json(paths["job"], asdict(job))
    atomic_write_text(paths["posting_snapshot"], _posting_snapshot(job, match, run_date), encoding="utf-8")
    write_json(paths["match"], asdict(match))
    write_json(
        paths["index"],
        _package_index(
            job,
            match,
            slug,
            paths,
            run_id,
            stable_id,
            fuzzy_key,
            state,
            application_status,
            False,
            ai_evaluation,
            review_list,
        ),
    )
    return {name: str(path) for name, path in paths.items()}


def write_daily_digest(
    summary: dict, items: list[dict], source_warnings: list[SourceWarning], run_date: date, root: Path = ROOT
) -> Path:
    env = _env(root)
    digest_dir = output_dir(root) / "daily-digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_path = digest_dir / f"{run_date}-digest.md"
    content = env.get_template("daily-digest.md.j2").render(
        run_date=run_date, summary=summary, jobs=items, source_warnings=source_warnings
    )
    atomic_write_text(digest_path, content.strip() + "\n", encoding="utf-8")
    return digest_path


def write_excluded_summary(
    excluded_items: list[dict], source_warnings: list[SourceWarning], run_date: date, root: Path = ROOT
) -> Path:
    env = _env(root)
    digest_dir = output_dir(root) / "daily-digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    path = digest_dir / f"{run_date}-excluded.md"
    content = env.get_template("excluded-summary.md.j2").render(
        run_date=run_date, excluded=excluded_items, source_warnings=source_warnings
    )
    atomic_write_text(path, content.strip() + "\n", encoding="utf-8")
    return path


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:80] or "job"


def _posting_snapshot(job: Job, match: MatchResult, run_date: date) -> str:
    lines = [
        f"# Saved Posting Snapshot - {job.title}",
        "",
        "This is the local posting snapshot saved when the job package was created. "
        "Use it if the external posting changes or disappears.",
        "",
        "## Snapshot Metadata",
        f"- Snapshot date: {run_date.isoformat()}",
        f"- Source: {_snapshot_value(job.source)}",
        f"- Source ID: {_snapshot_value(job.source_id)}",
        f"- External posting URL: {_snapshot_value(job.url)}",
        f"- Application URL: {_snapshot_value(job.application_url)}",
        f"- Match at capture: {match.total_score}% {match.category}",
        "",
        "## Posting Facts",
        f"- Company: {_snapshot_value(job.company)}",
        f"- Recruiter: {_snapshot_value(job.recruiter)}",
        f"- End client: {_snapshot_value(job.end_client)}",
        f"- Location: {_snapshot_value(job.location)}",
        f"- Remote/onsite: {_snapshot_value(job.remote)}",
        f"- Rate: {_snapshot_value(job.rate)}",
        f"- Workload: {_snapshot_value(job.workload)}",
        f"- Contract duration: {_snapshot_value(job.contract_duration)}",
        f"- Start date: {_snapshot_value(job.start_date)}",
        f"- Posted date: {_snapshot_value(job.posted_date)}",
        f"- Deadline: {_snapshot_value(job.deadline)}",
        f"- Languages: {_snapshot_value(', '.join(job.languages))}",
        "",
        "## Extracted Description",
        _snapshot_value(job.description),
        "",
    ]
    if job.raw_text and job.raw_text.strip() != job.description.strip():
        lines.extend(["## Raw Extracted Text", job.raw_text.strip(), ""])
    if job.extraction_notes:
        lines.extend(["## Extraction Notes", *[f"- {note}" for note in job.extraction_notes], ""])
    return "\n".join(lines).strip() + "\n"


def _snapshot_value(value: object) -> str:
    text = str(value or "").strip()
    return text or "Not listed"


def _env(root: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(templates_dir(root)),
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _package_index(
    job: Job,
    match: MatchResult,
    slug: str,
    paths: dict[str, Path],
    run_id: str,
    stable_id: str,
    fuzzy_key: str,
    state: str,
    application_status: str,
    materials_generated: bool,
    ai_evaluation: dict | None = None,
    review_list: bool = True,
) -> dict:
    item = {
        "package_id": stable_id or slug,
        "stable_id": stable_id,
        "fuzzy_key": fuzzy_key,
        "run_id": run_id,
        "title": job.title,
        "descriptive_title": job.title,
        "company": job.company,
        "recruiter": job.recruiter,
        "source": job.source,
        "source_id": job.source_id,
        "location": job.location,
        "remote": job.remote,
        "rate": job.rate,
        "workload": job.workload,
        "advised_salary_or_rate": "",
        "match_score": match.total_score,
        "match_category": match.category,
        "recommended_angle": match.recommended_angle,
        "concerns": match.concerns,
        "application_url": job.application_url,
        "source_url": job.url,
        "listing_key": JobStore.listing_key(job),
        "state": state,
        "application_status": application_status,
        "posting_status": "active",
        "review_list": review_list,
        "materials_generated": materials_generated,
        "material_status": "generated" if materials_generated else "missing",
        "paths": {name: str(path) for name, path in paths.items() if name != "index"},
    }
    if ai_evaluation:
        item.update(ai_evaluation)
    return item
