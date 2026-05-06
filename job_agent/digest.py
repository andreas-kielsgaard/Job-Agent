from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .models import GeneratedPackage, Job, MatchResult


def write_job_package(job: Job, match: MatchResult, package: GeneratedPackage, run_date: date, root: Path = ROOT) -> dict[str, str]:
    slug = slugify(job.title)
    base = root / "output" / str(run_date) / slug
    base.mkdir(parents=True, exist_ok=True)

    paths = {
        "job": base / "job.json",
        "cv": base / "cv-at-a-glance.md",
        "application": base / "application.md",
        "form_answers": base / "form-answers.md",
        "match": base / "match.json",
    }

    paths["job"].write_text(_json(asdict(job)), encoding="utf-8")
    paths["match"].write_text(_json(asdict(match)), encoding="utf-8")
    paths["cv"].write_text(package.cv, encoding="utf-8")
    paths["application"].write_text(package.application, encoding="utf-8")
    paths["form_answers"].write_text(package.form_answers.replace("[generated alongside this form-answer file]", str(paths["cv"])), encoding="utf-8")

    return {name: str(path) for name, path in paths.items()}


def write_daily_digest(items: list[dict], run_date: date, root: Path = ROOT) -> Path:
    env = Environment(
        loader=FileSystemLoader(root / "templates"),
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    output_dir = root / "output" / "daily-digests"
    output_dir.mkdir(parents=True, exist_ok=True)
    digest_path = output_dir / f"{run_date}-digest.md"
    content = env.get_template("daily-digest.md.j2").render(run_date=run_date, jobs=items)
    digest_path.write_text(content.strip() + "\n", encoding="utf-8")
    return digest_path


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:80] or "job"


def _json(data: dict) -> str:
    import json

    return json.dumps(data, indent=2, ensure_ascii=False, default=str)
