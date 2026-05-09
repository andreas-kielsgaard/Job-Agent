from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from job_agent.models import Job
from job_agent.services.job_board_recipe_service import (
    RecipeExtractionResult,
    extract_jobs_with_recipe,
    extract_jobs_with_recipe_from_url,
    load_job_board_recipe,
    quality_from_recipe_result,
)


@dataclass
class PreviewJob:
    title: str
    url: str
    location: str
    remote: str
    rate: str
    workload: str
    posted_date: str
    start_date: str
    languages: list[str]
    description_preview: str
    extraction_notes: list[str]


@dataclass
class RecipePreviewResult:
    recipe_source_name: str
    recipe_path: str
    recipe_status: str
    input_type: str
    input_value: str
    base_url: str
    mode_used: str
    extracted_job_count: int
    useful_titles: int
    generic_labels: int
    unique_urls: int
    average_description_length: int
    jobs: list[PreviewJob] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def preview_recipe(
    recipe_path: str | Path,
    input_value: str | Path,
    base_url: str = "",
    rendered: bool = False,
    static: bool = False,
    root: Path | None = None,
) -> RecipePreviewResult:
    if rendered and static:
        raise ValueError("Use either --rendered or --static, not both.")

    root = root or Path.cwd()
    resolved_recipe_path = _resolve_path(recipe_path, root)
    recipe = load_job_board_recipe(resolved_recipe_path)
    extraction = _run_preview_extraction(recipe, input_value, base_url=base_url, rendered=rendered, static=static, root=root)
    quality = quality_from_recipe_result(extraction, recipe)

    return RecipePreviewResult(
        recipe_source_name=recipe.source_name,
        recipe_path=str(resolved_recipe_path),
        recipe_status=_recipe_status(resolved_recipe_path, recipe.source_name),
        input_type=_input_type(str(input_value)),
        input_value=str(input_value),
        base_url=extraction.base_url,
        mode_used=extraction.mode_used,
        extracted_job_count=quality.candidate_count,
        useful_titles=quality.useful_title_count,
        generic_labels=quality.generic_title_count,
        unique_urls=quality.unique_url_count,
        average_description_length=quality.average_description_length,
        jobs=[_preview_job(job) for job in extraction.jobs],
        warnings=list(quality.warnings),
    )


def _run_preview_extraction(
    recipe,
    input_value: str | Path,
    base_url: str,
    rendered: bool,
    static: bool,
    root: Path,
) -> RecipeExtractionResult:
    value = str(input_value).strip()
    if value.startswith(("http://", "https://")):
        forced_rendered = True if rendered else False if static else None
        return extract_jobs_with_recipe_from_url(value, recipe, rendered=forced_rendered)

    if rendered:
        raise ValueError("--rendered can only be used with a public http(s) URL.")
    resolved_base_url = base_url.strip() or recipe.start_url.strip()
    if not resolved_base_url:
        raise ValueError("Testing a local HTML file requires --base-url or recipe.start_url.")

    path = _resolve_path(value, root)
    if not path.exists():
        raise ValueError(f"HTML fixture not found: {path}")

    warnings = []
    if recipe.mode == "rendered_html":
        warnings.append("Local fixture HTML ignores recipe mode: rendered_html.")
    html = path.read_text(encoding="utf-8")
    jobs = extract_jobs_with_recipe(html, base_url=resolved_base_url, recipe=recipe)
    return RecipeExtractionResult(
        jobs=jobs,
        base_url=resolved_base_url,
        mode_used="local_fixture_html",
        warnings=warnings,
    )


def _preview_job(job: Job) -> PreviewJob:
    return PreviewJob(
        title=job.title,
        url=job.url,
        location=job.location,
        remote=job.remote,
        rate=job.rate,
        workload=job.workload,
        posted_date=job.posted_date,
        start_date=job.start_date,
        languages=list(job.languages),
        description_preview=_description_preview(job.description),
        extraction_notes=list(job.extraction_notes),
    )


def _description_preview(description: str, limit: int = 280) -> str:
    text = " ".join(description.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _input_type(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return "public URL"
    normalized = value.replace("\\", "/").lower()
    if "output/recipe-calibration/" in normalized:
        return "local artifact"
    if "tests/fixtures/" in normalized:
        return "local fixture"
    return "local artifact"


def _recipe_status(recipe_path: Path, source_name: str) -> str:
    marker = str(recipe_path).replace("\\", "/").lower()
    if "/experimental/" in marker or "experimental" in source_name.lower():
        return "experimental"
    return "unspecified"


def _resolve_path(value: str | Path, root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path
