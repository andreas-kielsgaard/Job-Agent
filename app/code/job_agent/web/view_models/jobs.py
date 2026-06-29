from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from job_agent.application_status_store import APPLICATION_STATUSES, ApplicationStatusStore
from job_agent.application_store import ApplicationStore
from job_agent.config import ROOT
from job_agent.llm import LlmService
from job_agent.models import SeenJobRecord
from job_agent.run_store import RunRecord, RunStore
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.job_context_copy_service import JobContextCopyService
from job_agent.services.package_index_service import PackageIndexService
from job_agent.services.source_listing_index_store import SourceListingIndexRecord, SourceListingIndexStore
from job_agent.services.source_registry_service import SourceRegistryService
from job_agent.store import JobStore
from job_agent.web.formatting import markdown_to_html

MATERIAL_SECTIONS = [
    ("focused_cv", "Focused one-page CV", "One-page CV"),
    ("cv", "At-a-glance CV", "CV"),
    ("application", "Application text", "Application"),
    ("form_answers", "Form answers", "Forms"),
    ("match_analysis", "Match analysis", "Analysis"),
]


def build_jobs_view(filters: dict[str, Any] | None = None, root: Path = ROOT) -> dict:
    filters = filters or {}
    service = PackageIndexService(root)
    run_labels = _run_label_map(root)
    all_packages = [_with_run_context(service, job, run_labels) for job in service.list_packages()]
    all_jobs = [_with_applied_match_override(job) for job in _with_indexed_listing_jobs(root, all_packages)]
    jobs = _filter_jobs(all_jobs, filters)
    if filters.get("dedupe", True):
        jobs = _dedupe_latest(jobs)
    jobs = [_with_job_row_display(job, service) for job in jobs]
    source_options = _source_options(root, jobs, all_jobs)
    run_options = _run_options(root, jobs, all_jobs)
    normalized_filters = _normalized_filters(filters)
    return {
        "title": "Jobs",
        "jobs": jobs,
        "today": date.today().isoformat(),
        "filters": {
            "app_statuses": normalized_filters["app_status_includes"],
            "app_status_includes": normalized_filters["app_status_includes"],
            "app_status_excludes": normalized_filters["app_status_excludes"],
            "categories": normalized_filters["category_includes"],
            "category_includes": normalized_filters["category_includes"],
            "category_excludes": normalized_filters["category_excludes"],
            "source_ids": normalized_filters["source_id_includes"],
            "source_id_includes": normalized_filters["source_id_includes"],
            "source_id_excludes": normalized_filters["source_id_excludes"],
            "run_ids": normalized_filters["run_id_includes"],
            "run_id_includes": normalized_filters["run_id_includes"],
            "run_id_excludes": normalized_filters["run_id_excludes"],
            "date_from": str(filters.get("date_from") or ""),
            "date_to": str(filters.get("date_to") or ""),
            "source": str(filters.get("source") or ""),
            "material_statuses": normalized_filters["material_status_includes"],
            "material_status_includes": normalized_filters["material_status_includes"],
            "material_status_excludes": normalized_filters["material_status_excludes"],
            "posting_status_includes": normalized_filters["posting_status_includes"],
            "posting_status_excludes": normalized_filters["posting_status_excludes"],
            "ai_prioritized": bool(filters.get("ai_prioritized")),
            "show_condition_excluded": bool(filters.get("show_condition_excluded")),
            "dedupe": bool(filters.get("dedupe", True)),
        },
        "source_options": source_options,
        "run_options": run_options,
        "result_count": len(jobs),
        "llm_configured": LlmService(root).is_configured(),
    }


def build_job_detail_view(job_id: str, run_id: str = "", root: Path = ROOT) -> dict:
    service = PackageIndexService(root)
    package = service.find_package(job_id, run_id)
    if not package:
        raise KeyError(job_id)
    files = service.read_package_files(package)
    job_payload = _json_payload(files.get("job", ""))
    match_payload = _json_payload(files.get("match", ""))
    status = ApplicationStatusStore(root).get(job_id)
    application_record = ApplicationStore(root).get(str(package.get("stable_id") or job_id))
    application_detail_url = ""
    if (
        application_record
        or str(package.get("application_status") or "") == "applied"
        or (status and status.status == "applied")
    ):
        application_detail_url = f"/applications/{quote(str(package.get('stable_id') or job_id), safe='')}"
    job_copy_context = JobContextCopyService(root).build(package, files, status)
    return {
        "title": f"Job - {package.get('title') or package.get('stable_id') or 'Detail'}",
        "package": package,
        "files": files,
        "job_copy_context": job_copy_context,
        "job_detail": _job_detail(job_payload, package),
        "match_summary": _match_summary(match_payload, package),
        "material_sections": _material_sections(files),
        "generation_outputs": _generation_outputs(files, package),
        "cv_artifacts": _cv_artifacts(package),
        "posting_snapshot": _artifact(package, "posting_snapshot", "Saved posting snapshot"),
        "form_answer_items": _form_answer_items(files.get("form_answers", "")),
        "status": status,
        "application_detail_url": application_detail_url,
        "statuses": sorted(APPLICATION_STATUSES),
        "render_md": markdown_to_html,
        "cv_reference": CvReferenceService(root).get_cv_reference(),
        "score_details": _score_details(package),
        "llm_configured": LlmService(root).is_configured(),
    }


def _json_payload(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _job_detail(job: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    description = _display_text(_first_text(job.get("description"), job.get("raw_text")))
    return {
        "description": description,
        "description_blocks": _description_blocks(description),
        "facts": _fact_items(job, package),
        "missing_facts": _missing_fact_items(job),
        "tag_groups": _tag_groups(job),
        "extraction_notes": _text_list(job.get("extraction_notes")),
        "source_confidence": _display_text(job.get("source_confidence") or ""),
        "freshness_confidence": _display_text(job.get("freshness_confidence") or ""),
        "source_url": _display_text(job.get("url") or package.get("source_url") or ""),
        "application_url": _display_text(job.get("application_url") or package.get("application_url") or ""),
    }


def _match_summary(match: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    matched_keywords = _text_list(match.get("matched_keywords"))
    review_triggers = _text_list(match.get("review_trigger_labels") or match.get("review_triggers"))
    confidence = _display_text(match.get("deterministic_confidence") or "")
    score = match.get("total_score", package.get("match_score"))
    return {
        "score": score,
        "display_score": package.get("match_score", score),
        "deterministic_score": package.get("deterministic_match_score", score),
        "ai_score": package.get("ai_match_score"),
        "category": _display_text(match.get("category") or package.get("match_category") or "not_scored"),
        "recommended_angle": _display_text(match.get("recommended_angle") or package.get("recommended_angle") or ""),
        "matched_keywords": matched_keywords,
        "review_triggers": review_triggers,
        "confidence": confidence,
        "condition_exclusions": _text_list(match.get("condition_exclusions") or package.get("condition_exclusions")),
        "condition_preferences": _text_list(match.get("condition_preferences") or package.get("condition_preferences")),
    }


def _material_sections(files: dict[str, str]) -> list[dict[str, str]]:
    sections = []
    for key, label, short_label in MATERIAL_SECTIONS:
        content = files.get(key, "")
        if not content.strip():
            continue
        sections.append(
            {
                "key": key,
                "label": label,
                "short_label": short_label,
                "content": content,
            }
        )
    return sections


def _generation_outputs(files: dict[str, str], package: dict[str, Any]) -> list[dict[str, str]]:
    cv_ready = _artifact_available(package, "focused_cv_pdf") and _artifact_available(package, "focused_cv_tex")
    outputs = [
        (
            "Targeted one-page CV",
            "LaTeX source plus a PDF preview/download, focused from the master CV for this posting.",
            cv_ready,
        ),
        (
            "Application text",
            "A recruiter-facing draft that can be copied or edited locally before you use it.",
            bool(files.get("application", "").strip()),
        ),
        (
            "Form answers",
            "Copyable answers for common application fields, including the generated CV file path.",
            bool(files.get("form_answers", "").strip()),
        ),
        (
            "Match evidence",
            "The at-a-glance CV and deterministic match analysis used to check the fit.",
            bool(files.get("cv", "").strip() or files.get("match_analysis", "").strip()),
        ),
    ]
    return [
        {
            "label": label,
            "description": description,
            "status": "Ready" if ready else "Will be created",
            "tone": "strong" if ready else "waiting",
        }
        for label, description, ready in outputs
    ]


def _cv_artifacts(package: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {
        "pdf": _artifact(package, "focused_cv_pdf", "PDF"),
        "tex": _artifact(package, "focused_cv_tex", "LaTeX"),
        "markdown": _artifact(package, "focused_cv", "Markdown"),
        "html": _artifact(package, "focused_cv_html", "HTML"),
    }


def _artifact(package: dict[str, Any], key: str, label: str) -> dict[str, str]:
    if not _artifact_available(package, key):
        return {"key": key, "label": label, "url": "", "download_url": "", "filename": ""}
    path = Path(str(package.get("paths", {}).get(key) or ""))
    return {
        "key": key,
        "label": label,
        "url": _package_file_url(package, key),
        "download_url": _package_file_url(package, key, download=True),
        "filename": path.name,
    }


def _artifact_available(package: dict[str, Any], key: str) -> bool:
    path_text = str(package.get("paths", {}).get(key) or "")
    if not path_text:
        return False
    path = Path(path_text)
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


def _package_file_url(package: dict[str, Any], key: str, download: bool = False) -> str:
    stable_id = quote(str(package.get("stable_id") or package.get("package_id") or ""), safe="")
    query = {}
    if package.get("run_id"):
        query["run_id"] = str(package.get("run_id"))
    if download:
        query["download"] = "1"
    suffix = f"?{urlencode(query)}" if query else ""
    return f"/jobs/{stable_id}/files/{key}{suffix}"


def _form_answer_items(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    label = ""
    lines: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            continue
        if _looks_like_answer_label(stripped):
            _append_form_answer(items, label, lines)
            label = stripped[:-1].strip()
            lines = []
            continue
        if label:
            lines.append(raw_line)
    _append_form_answer(items, label, lines)
    return items


def _looks_like_answer_label(value: str) -> bool:
    if not value.endswith(":") or len(value) > 80:
        return False
    return bool(re.match(r"^[A-Z][A-Za-z0-9 /().'-]+:$", value))


def _append_form_answer(items: list[dict[str, Any]], label: str, lines: list[str]) -> None:
    value = "\n".join(lines).strip()
    if not label or not value:
        return
    item_id = f"form-answer-{len(items) + 1}-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')}"
    items.append(
        {
            "id": item_id,
            "label": label,
            "value": value,
            "preview": _clip_text(value, 180),
            "is_long": len(value) > 260 or "\n" in value.strip(),
        }
    )


def _clip_text(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "."


def _fact_items(job: dict[str, Any], package: dict[str, Any]) -> list[dict[str, str]]:
    fields = [
        ("Company", job.get("company") or package.get("company")),
        ("Recruiter", job.get("recruiter") or package.get("recruiter")),
        ("End client", job.get("end_client")),
        ("Source", job.get("source") or package.get("source")),
        ("Location", job.get("location") or package.get("location")),
        ("Remote", job.get("remote") or package.get("remote")),
        ("Rate", job.get("rate") or package.get("rate")),
        ("Workload", job.get("workload") or package.get("workload")),
        ("Duration", job.get("contract_duration")),
        ("Start", job.get("start_date")),
        ("Posted", _display_date(job.get("posted_date"))),
        ("Deadline", job.get("deadline")),
        ("First seen", job.get("first_seen_date")),
        ("Role category", job.get("role_category")),
        ("Seniority", job.get("seniority")),
    ]
    return [{"label": label, "value": _display_text(value)} for label, value in fields if _has_real_value(value)]


def _missing_fact_items(job: dict[str, Any]) -> list[str]:
    fields = [
        ("Company", job.get("company")),
        ("Recruiter", job.get("recruiter")),
        ("End client", job.get("end_client")),
        ("Duration", job.get("contract_duration")),
        ("Start", job.get("start_date")),
        ("Deadline", job.get("deadline")),
    ]
    return [label for label, value in fields if not _has_real_value(value)]


def _tag_groups(job: dict[str, Any]) -> list[dict[str, Any]]:
    groups = [
        ("Required skills", _text_list(job.get("required_skills"))),
        ("Required modules", _text_list(job.get("required_modules"))),
        ("Languages", _text_list(job.get("required_languages") or job.get("languages"))),
    ]
    return [{"label": label, "items": items} for label, items in groups if items]


def _description_blocks(description: str) -> list[dict[str, Any]]:
    text = _description_with_breaks(description)
    if not text:
        return []

    blocks: list[dict[str, Any]] = []
    heading = "Overview"
    paragraphs: list[str] = []
    headings_with_colons = {
        "skills": "Skills",
        "details": "Details",
        "interested?": "Interested?",
        "let op": "Job fraud notice",
        "important": "Job fraud notice",
    }
    headings_without_colons = {"key responsibilities": "Key responsibilities"}

    def flush() -> None:
        nonlocal paragraphs
        cleaned = [item for item in paragraphs if item]
        if cleaned:
            blocks.append({"heading": heading, "paragraphs": cleaned})
        paragraphs = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower().rstrip(":")
        if lowered in headings_without_colons:
            flush()
            heading = headings_without_colons[lowered]
            continue
        colon_match = re.match(r"^([^:]{2,32}):\s*(.*)$", line)
        if colon_match:
            key = colon_match.group(1).strip().lower()
            if key in headings_with_colons:
                flush()
                heading = headings_with_colons[key]
                if colon_match.group(2).strip():
                    paragraphs.append(colon_match.group(2).strip())
                continue
        paragraphs.append(line)
    flush()
    return blocks or [{"heading": "Overview", "paragraphs": [description]}]


def _description_with_breaks(text: str) -> str:
    result = _display_text(text)
    for marker in ["Skills:", "Details:", "Interested?", "Let op:", "Important:"]:
        result = re.sub(rf"\s+({re.escape(marker)})", "\n" + r"\1", result)
    result = re.sub(r"\s+(Key Responsibilities)\s+", "\n" + r"\1" + "\n", result)
    return result.strip()


def _display_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.match(r"^(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2})", text)
    if match:
        suffix = " UTC" if text.endswith("Z") else ""
        return f"{match.group(1)} {match.group(2)}{suffix}"
    return text


def _first_text(*values: Any) -> str:
    for value in values:
        text = _display_text(value)
        if text.strip():
            return text
    return ""


def _display_text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if "\u00c3" in text or "\u00e2\u20ac" in text:
        try:
            repaired = text.encode("cp1252").decode("utf-8")
        except UnicodeError:
            return text
        if repaired.count("\ufffd") <= text.count("\ufffd"):
            return repaired
    return text


def _has_real_value(value: Any) -> bool:
    text = _display_text(value)
    return bool(text) and text.lower() not in {"unknown", "not listed", "n/a", "none", "-"}


def _score_details(package: dict[str, Any]) -> dict[str, Any]:
    raw_components = package.get("components") or package.get("match_components") or {}
    components: list[dict[str, Any]] = []
    if isinstance(raw_components, dict):
        components = [
            {
                "label": str(key).replace("_", " ").title(),
                "value": value,
                "tone": "positive" if _numeric(value) > 0 else "negative" if _numeric(value) < 0 else "neutral",
            }
            for key, value in raw_components.items()
            if _numeric(value) != 0
        ]
    elif isinstance(raw_components, list):
        components = [item for item in raw_components if isinstance(item, dict)]
    return {
        "components": components,
        "reasons": _text_list(package.get("reasons") or package.get("match_reasons")),
        "concerns": _text_list(package.get("concerns") or package.get("match_concerns")),
        "missing_information": _text_list(package.get("missing_information") or package.get("match_missing_info")),
    }


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_display_text(item) for item in value if _display_text(item)]
    if isinstance(value, str) and value.strip():
        return [_display_text(item) for item in value.split(";") if _display_text(item)]
    return []


def _numeric(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _filter_jobs(jobs: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = _normalized_filters(filters)
    app_status_includes = set(normalized["app_status_includes"])
    app_status_excludes = set(normalized["app_status_excludes"])
    category_includes = set(normalized["category_includes"])
    category_excludes = set(normalized["category_excludes"])
    source_id_includes = set(normalized["source_id_includes"])
    source_id_excludes = set(normalized["source_id_excludes"])
    run_id_includes = set(normalized["run_id_includes"])
    run_id_excludes = set(normalized["run_id_excludes"])
    material_status_includes = set(normalized["material_status_includes"])
    material_status_excludes = set(normalized["material_status_excludes"])
    posting_status_includes = set(normalized["posting_status_includes"])
    posting_status_excludes = set(normalized["posting_status_excludes"])
    source_text = str(filters.get("source") or "").strip().lower()
    date_from = _date(filters.get("date_from"))
    date_to = _date(filters.get("date_to"))
    show_condition_excluded = bool(filters.get("show_condition_excluded"))

    result = []
    for job in jobs:
        if not show_condition_excluded and _text_list(job.get("condition_exclusions")):
            continue
        if not _matches_tri_filter(job.get("application_status"), app_status_includes, app_status_excludes):
            continue
        if not _matches_tri_filter(job.get("match_category"), category_includes, category_excludes):
            continue
        if not _matches_tri_filter(job.get("source_id"), source_id_includes, source_id_excludes):
            continue
        if not _matches_tri_filter(job.get("run_id"), run_id_includes, run_id_excludes):
            continue
        if not _matches_tri_filter(job.get("material_status"), material_status_includes, material_status_excludes):
            continue
        if not _matches_tri_filter(job.get("posting_status"), posting_status_includes, posting_status_excludes):
            continue
        if filters.get("ai_prioritized") and not _truthy(job.get("ai_should_prioritize")):
            continue
        if (
            source_text
            and source_text
            not in " ".join(
                [str(job.get("source") or ""), str(job.get("source_id") or ""), str(job.get("source_url") or "")]
            ).lower()
        ):
            continue
        run_date = _date(job.get("run_date"))
        if date_from and (not run_date or run_date < date_from):
            continue
        if date_to and (not run_date or run_date > date_to):
            continue
        result.append(job)
    return result


def _normalized_filters(filters: dict[str, Any]) -> dict[str, list[str]]:
    category_includes = _values(filters, "category_includes", "categories")
    category_excludes = _values(filters, "category_excludes")
    posting_status_includes = _values(filters, "posting_status_includes")
    posting_status_excludes = _values(filters, "posting_status_excludes")
    if not category_includes and not category_excludes:
        category_excludes = ["weak", "excluded"]
    if not posting_status_includes and not posting_status_excludes:
        posting_status_excludes = ["no_longer_posted"]
    return {
        "app_status_includes": _values(filters, "app_status_includes", "app_statuses"),
        "app_status_excludes": _values(filters, "app_status_excludes"),
        "category_includes": category_includes,
        "category_excludes": category_excludes,
        "source_id_includes": _values(filters, "source_id_includes", "source_ids"),
        "source_id_excludes": _values(filters, "source_id_excludes"),
        "run_id_includes": _values(filters, "run_id_includes", "run_ids"),
        "run_id_excludes": _values(filters, "run_id_excludes"),
        "material_status_includes": _values(filters, "material_status_includes", "material_statuses"),
        "material_status_excludes": _values(filters, "material_status_excludes"),
        "posting_status_includes": posting_status_includes,
        "posting_status_excludes": posting_status_excludes,
    }


def _values(filters: dict[str, Any], *keys: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for key in keys:
        value = filters.get(key)
        items = value if isinstance(value, list) else [value] if value else []
        for item in items:
            text = str(item or "").strip()
            if text and text not in seen:
                result.append(text)
                seen.add(text)
    return result


def _matches_tri_filter(value: Any, includes: set[str], excludes: set[str]) -> bool:
    text = str(value or "")
    if includes and text not in includes:
        return False
    return text not in excludes


def _with_applied_match_override(job: dict[str, Any]) -> dict[str, Any]:
    if str(job.get("application_status") or "") != "applied":
        return job
    enriched = dict(job)
    enriched["original_match_score"] = job.get("match_score")
    enriched["original_match_category"] = job.get("match_category")
    enriched["match_score"] = 100
    enriched["match_category"] = "strong"
    enriched["match_display_note"] = "Applied override"
    return enriched


def _with_run_context(service: PackageIndexService, job: dict[str, Any], run_labels: dict[str, str]) -> dict[str, Any]:
    enriched = dict(job)
    run_id = str(enriched.get("run_id") or "")
    enriched["run_date"] = str(service.infer_package_date(job))
    enriched["run_label"] = run_labels.get(run_id) or (f"Daily Run {enriched['run_date']}" if run_id else "")
    enriched["has_package"] = True
    enriched["can_update_status"] = True
    enriched["posting_status"] = str(enriched.get("posting_status") or "active")
    enriched["match_category"] = str(enriched.get("match_category") or "not_scored")
    enriched["deterministic_match_score"] = _numeric(enriched.get("deterministic_match_score")) or _numeric(
        enriched.get("match_score")
    )
    enriched["application_status"] = str(enriched.get("application_status") or "unreviewed")
    enriched["source_url"] = str(
        enriched.get("source_url") or enriched.get("url") or enriched.get("application_url") or ""
    )
    return enriched


def _with_job_row_display(job: dict[str, Any], service: PackageIndexService | None = None) -> dict[str, Any]:
    enriched = dict(job)
    detail_url = _job_detail_url(enriched) if enriched.get("has_package") else ""
    source_url = _display_text(
        enriched.get("source_url") or enriched.get("url") or enriched.get("application_url") or ""
    )
    row_target_url = detail_url or source_url
    pay_display = _display_text(enriched.get("rate") or enriched.get("advised_salary_or_rate") or "")
    enriched["detail_url"] = detail_url
    enriched["row_target_url"] = row_target_url
    enriched["row_target_label"] = "Open detail" if detail_url else "Open posting" if source_url else ""
    enriched["pay_display"] = pay_display if _has_real_value(pay_display) else "Not listed"
    enriched["pay_sort_value"] = _pay_sort_value(pay_display)
    enriched["preview_description"] = _job_preview_description(enriched, service)
    enriched["condition_exclusions"] = _text_list(enriched.get("condition_exclusions"))
    enriched["condition_preferences"] = _text_list(enriched.get("condition_preferences"))
    enriched["has_condition_warning"] = bool(enriched["condition_preferences"])
    enriched["has_condition_exclusion"] = bool(enriched["condition_exclusions"])
    return enriched


def _job_detail_url(job: dict[str, Any]) -> str:
    stable_id = str(job.get("stable_id") or job.get("package_id") or "").strip()
    if not stable_id:
        return ""
    run_id = str(job.get("run_id") or "").strip()
    url = f"/jobs/{quote(stable_id, safe='')}"
    if run_id:
        url = f"{url}?run_id={quote(run_id, safe='')}"
    return url


def _job_preview_description(job: dict[str, Any], service: PackageIndexService | None) -> str:
    if not service or not job.get("has_package"):
        return ""
    payload = _json_payload(service.read_package_files(job).get("job", ""))
    description = _display_text(payload.get("description"))
    if not description:
        return ""
    text = _description_with_breaks(description)
    max_length = 1600
    if len(text) <= max_length:
        return text
    return f"{text[:max_length].rstrip()}..."


def _pay_sort_value(value: Any) -> float:
    text = _display_text(value)
    if not _has_real_value(text):
        return -1
    values: list[float] = []
    for match in re.finditer(r"(?i)(\d+(?:[.,]\d+)?)(\s*k\b)?", text):
        amount = _pay_number(match.group(1))
        if amount <= 0:
            continue
        if match.group(2):
            amount *= 1000
        values.append(amount)
    if not values:
        return -1
    amount = max(values)
    lowered = text.lower()
    if any(marker in lowered for marker in ["hour", "/h", " per h"]):
        amount *= 8
    elif any(marker in lowered for marker in ["week", "/wk", "weekly"]):
        amount /= 5
    elif any(marker in lowered for marker in ["month", "/mo", "monthly"]):
        amount /= 21
    elif any(marker in lowered for marker in ["year", "annum", "annual", "/yr", "p.a", " pa"]):
        amount /= 220
    return round(amount, 2)


def _pay_number(raw: str) -> float:
    text = raw.strip()
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        pieces = text.split(",")
        text = "".join(pieces) if len(pieces[-1]) == 3 else text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0


def _with_indexed_listing_jobs(root: Path, package_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    package_listing_keys = {str(job.get("listing_key") or "") for job in package_jobs if job.get("listing_key")}
    package_stable_ids = {str(job.get("stable_id") or "") for job in package_jobs if job.get("stable_id")}
    try:
        seen_records = JobStore(root, create=False).list_seen_records()
    except ValueError:
        seen_records = []
    seen_by_listing = {record.listing_key: record for record in seen_records if record.listing_key}
    statuses = _application_statuses(root)
    source_lookup = _source_lookup(root)
    rows = list(package_jobs)

    indexed_listing_keys: set[str] = set()
    for summary in SourceListingIndexStore(root).list_all():
        for listing in summary.listings:
            indexed_listing_keys.add(listing.listing_key)
            if listing.listing_key in package_listing_keys:
                continue
            rows.append(
                _listing_job_row(
                    listing,
                    seen_by_listing.get(listing.listing_key),
                    statuses,
                    source_lookup,
                )
            )

    for record in seen_records:
        if record.posting_status != "no_longer_posted":
            continue
        if record.listing_key in package_listing_keys or record.listing_key in indexed_listing_keys:
            continue
        if record.stable_id in package_stable_ids:
            continue
        rows.append(_seen_history_row(record, statuses, source_lookup))
    return rows


def _listing_job_row(
    listing: SourceListingIndexRecord,
    seen: SeenJobRecord | None,
    statuses: dict[str, str],
    source_lookup: dict[str, dict[str, str]],
) -> dict[str, Any]:
    stable_id = seen.stable_id if seen else f"listing-{listing.listing_key}"
    source_id = listing.source_id or _source_id_for(listing.source, listing.url, source_lookup)
    posting_status = seen.posting_status if seen else listing.posting_status
    return {
        "stable_id": stable_id,
        "package_id": stable_id,
        "listing_key": listing.listing_key,
        "run_id": "",
        "run_label": "Indexed listing",
        "run_date": str(listing.last_indexed_at or "")[:10],
        "title": listing.title or (seen.title if seen else "Unknown posting"),
        "company": seen.company if seen else "Unknown",
        "recruiter": "",
        "source": listing.source,
        "source_id": source_id,
        "source_url": listing.url,
        "application_url": listing.url,
        "url": listing.url,
        "location": "Not listed",
        "remote": "Not listed",
        "match_score": 0,
        "match_category": "not_scored",
        "state": "detail reviewed" if seen else "indexed only",
        "application_status": statuses.get(stable_id, "unreviewed"),
        "posting_status": posting_status or "active",
        "material_status": "missing",
        "ai_should_prioritize": False,
        "has_package": False,
        "can_update_status": bool(seen and stable_id in statuses),
    }


def _seen_history_row(
    record: SeenJobRecord, statuses: dict[str, str], source_lookup: dict[str, dict[str, str]]
) -> dict[str, Any]:
    source_id = _source_id_for(record.source, record.url, source_lookup)
    return {
        "stable_id": record.stable_id,
        "package_id": record.stable_id,
        "listing_key": record.listing_key,
        "run_id": "",
        "run_label": "Historical posting",
        "run_date": str(record.last_seen_date or "")[:10],
        "title": record.title,
        "company": record.company,
        "recruiter": "",
        "source": record.source,
        "source_id": source_id,
        "source_url": record.url,
        "application_url": record.url,
        "url": record.url,
        "location": "Not listed",
        "remote": "Not listed",
        "match_score": 0,
        "match_category": "not_scored",
        "state": "historical",
        "application_status": statuses.get(record.stable_id, "unreviewed"),
        "posting_status": record.posting_status or "no_longer_posted",
        "material_status": "missing",
        "ai_should_prioritize": False,
        "has_package": False,
        "can_update_status": record.stable_id in statuses,
    }


def _dedupe_latest(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for job in jobs:
        stable_id = str(job.get("listing_key") or job.get("stable_id") or job.get("package_id") or "")
        existing = by_id.get(stable_id)
        if not stable_id:
            continue
        if not existing or (str(job.get("run_date") or ""), str(job.get("run_id") or "")) > (
            str(existing.get("run_date") or ""),
            str(existing.get("run_id") or ""),
        ):
            by_id[stable_id] = job
    return sorted(by_id.values(), key=lambda item: (item.get("match_score", 0), item.get("title", "")), reverse=True)


def _source_options(
    root: Path, filtered_jobs: list[dict[str, Any]], all_jobs: list[dict[str, Any]]
) -> list[dict[str, str]]:
    options = {source.id: source.name for source in SourceRegistryService(root).list_sources()}
    for job in [*all_jobs, *filtered_jobs]:
        source_id = str(job.get("source_id") or "").strip()
        if not source_id:
            continue
        options[source_id] = str(job.get("source") or source_id)
    return [{"id": key, "label": label} for key, label in sorted(options.items(), key=lambda item: item[1].lower())]


def _run_options(
    root: Path, filtered_jobs: list[dict[str, Any]], all_jobs: list[dict[str, Any]]
) -> list[dict[str, str]]:
    labels = _run_label_map(root)
    by_run: dict[str, str] = {}
    for job in [*all_jobs, *filtered_jobs]:
        run_id = str(job.get("run_id") or "")
        if not run_id:
            continue
        by_run[run_id] = str(job.get("run_label") or labels.get(run_id) or f"Daily Run {job.get('run_date') or run_id}")
    return [
        {"id": run_id, "label": label}
        for run_id, label in sorted(by_run.items(), key=lambda item: item[1].lower(), reverse=True)
    ]


def _run_label_map(root: Path) -> dict[str, str]:
    try:
        records = RunStore(root).list_runs(include_archived=True, include_deleted=True, include_tests=True)
    except ValueError:
        records = []
    return {record.run_id: _run_label(record) for record in records}


def _run_label(record: RunRecord) -> str:
    date_text = str(record.started_at or record.run_id)[:10]
    time_text = str(record.started_at or "")[11:16]
    suffix = f" {time_text}" if time_text else ""
    options = record.options if isinstance(record.options, dict) else {}
    if record.is_test or options.get("is_test"):
        prefix = "Test Run"
    elif options.get("append_to_daily_run"):
        prefix = "Source Ingestion Run"
    else:
        prefix = "Daily Run"
    return f"{prefix} {date_text}{suffix}"


def _application_statuses(root: Path) -> dict[str, str]:
    try:
        return {record.stable_id: record.status for record in ApplicationStatusStore(root).list_all()}
    except ValueError:
        return {}


def _source_lookup(root: Path) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for source in SourceRegistryService(root).list_sources():
        item = {"id": source.id, "name": source.name, "url": source.url}
        lookup[source.id.strip().lower()] = item
        lookup[source.name.strip().lower()] = item
    return lookup


def _source_id_for(source_name: str, url: str, lookup: dict[str, dict[str, str]]) -> str:
    key = str(source_name or "").strip().lower()
    if key in lookup:
        return lookup[key]["id"]
    for item in lookup.values():
        if item["url"] and url and _same_host_path(item["url"], url):
            return item["id"]
    return key


def _same_host_path(left: str, right: str) -> bool:
    from urllib.parse import urlparse

    left_parsed = urlparse(left if "://" in left else f"https://{left}")
    right_parsed = urlparse(right if "://" in right else f"https://{right}")
    left_host = left_parsed.netloc.lower().removeprefix("www.")
    right_host = right_parsed.netloc.lower().removeprefix("www.")
    if not left_host or left_host != right_host:
        return False
    left_path = left_parsed.path.rstrip("/")
    right_path = right_parsed.path.rstrip("/")
    return not left_path or right_path == left_path or right_path.startswith(f"{left_path}/")


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}
