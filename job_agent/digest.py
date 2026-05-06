from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .models import GeneratedPackage, Job, MatchResult, SourceWarning


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
) -> dict[str, str]:
    slug = slugify(job.title)
    base = root / "output" / str(run_date) / slug
    base.mkdir(parents=True, exist_ok=True)

    paths = {
        "job": base / "job.json",
        "cv": base / "cv-at-a-glance.md",
        "application": base / "application.md",
        "form_answers": base / "form-answers.md",
        "match_analysis": base / "match-analysis.md",
        "match": base / "match.json",
        "index": base / "index.json",
    }

    paths["job"].write_text(_json(asdict(job)), encoding="utf-8")
    paths["match"].write_text(_json(asdict(match)), encoding="utf-8")
    paths["cv"].write_text(package.cv, encoding="utf-8")
    paths["application"].write_text(package.application, encoding="utf-8")
    paths["match_analysis"].write_text(package.match_analysis, encoding="utf-8")
    paths["form_answers"].write_text(package.form_answers.replace("[generated alongside this form-answer file]", str(paths["cv"])), encoding="utf-8")
    paths["index"].write_text(
        _json(
            {
                "package_id": stable_id or slug,
                "stable_id": stable_id,
                "fuzzy_key": fuzzy_key,
                "run_id": run_id,
                "title": job.title,
                "descriptive_title": job.title,
                "company": job.company,
                "recruiter": job.recruiter,
                "location": job.location,
                "remote": job.remote,
                "rate": job.rate,
                "advised_salary_or_rate": "",
                "match_score": match.total_score,
                "match_category": match.category,
                "recommended_angle": match.recommended_angle,
                "concerns": match.concerns,
                "application_url": job.application_url,
                "source_url": job.url,
                "state": state,
                "application_status": application_status,
                "materials_generated": True,
                "paths": {name: str(path) for name, path in paths.items() if name != "index"},
            }
        ),
        encoding="utf-8",
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
) -> dict[str, str]:
    slug = slugify(job.title)
    base = root / "output" / str(run_date) / slug
    base.mkdir(parents=True, exist_ok=True)

    paths = {
        "job": base / "job.json",
        "match": base / "match.json",
        "index": base / "index.json",
    }
    paths["job"].write_text(_json(asdict(job)), encoding="utf-8")
    paths["match"].write_text(_json(asdict(match)), encoding="utf-8")
    paths["index"].write_text(
        _json(
            {
                "package_id": stable_id or slug,
                "stable_id": stable_id,
                "fuzzy_key": fuzzy_key,
                "run_id": run_id,
                "title": job.title,
                "descriptive_title": job.title,
                "company": job.company,
                "recruiter": job.recruiter,
                "location": job.location,
                "remote": job.remote,
                "rate": job.rate,
                "advised_salary_or_rate": "",
                "match_score": match.total_score,
                "match_category": match.category,
                "recommended_angle": match.recommended_angle,
                "concerns": match.concerns,
                "application_url": job.application_url,
                "source_url": job.url,
                "state": state,
                "application_status": application_status,
                "materials_generated": False,
                "paths": {name: str(path) for name, path in paths.items() if name != "index"},
            }
        ),
        encoding="utf-8",
    )
    return {name: str(path) for name, path in paths.items()}


def write_daily_digest(summary: dict, items: list[dict], source_warnings: list[SourceWarning], run_date: date, root: Path = ROOT) -> Path:
    env = _env(root)
    output_dir = root / "output" / "daily-digests"
    output_dir.mkdir(parents=True, exist_ok=True)
    digest_path = output_dir / f"{run_date}-digest.md"
    content = env.get_template("daily-digest.md.j2").render(run_date=run_date, summary=summary, jobs=items, source_warnings=source_warnings)
    digest_path.write_text(content.strip() + "\n", encoding="utf-8")
    return digest_path


def write_excluded_summary(excluded_items: list[dict], source_warnings: list[SourceWarning], run_date: date, root: Path = ROOT) -> Path:
    env = _env(root)
    output_dir = root / "output" / "daily-digests"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{run_date}-excluded.md"
    content = env.get_template("excluded-summary.md.j2").render(run_date=run_date, excluded=excluded_items, source_warnings=source_warnings)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:80] or "job"


def _env(root: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(root / "templates"),
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _json(data: dict) -> str:
    import json

    return json.dumps(data, indent=2, ensure_ascii=False, default=str)
