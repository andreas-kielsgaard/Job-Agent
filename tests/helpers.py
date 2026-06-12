from __future__ import annotations

from datetime import date
from pathlib import Path

from job_agent.digest import write_job_package
from job_agent.models import GeneratedPackage, Job, MatchResult


EURSAP_SOURCE = {
    "id": "eursap-jobs",
    "name": "Eursap Jobs",
    "kind": "recipe",
    "status": "testing",
    "url": "https://eursap.eu/jobs",
    "recipe_path": "sources/recipes/experimental/eursap-jobs.yaml",
    "added_at": "2026-05-09",
    "enabled": False,
    "notes": "Test fixture source.",
    "tags": ["sap", "recipe", "live-calibrated"],
}

WHITEHALL_SOURCE = {
    "id": "whitehall-sap-contract",
    "name": "Whitehall Resources SAP Jobs",
    "kind": "recipe",
    "status": "testing",
    "url": "https://www.whitehallresources.com/sap-jobs/",
    "recipe_path": "sources/recipes/experimental/whitehall-sap-contract.yaml",
    "added_at": "2026-05-09",
    "enabled": False,
    "notes": "Test fixture source.",
    "tags": ["sap", "recipe", "live-calibrated"],
}

SAMPLE_SOURCE = {
    "id": "sample-jobs",
    "name": "Sample Jobs",
    "kind": "local_yaml",
    "status": "ready",
    "url": "",
    "recipe_path": "",
    "added_at": "2026-05-09",
    "enabled": True,
    "notes": "Test fixture local YAML source.",
    "tags": ["local", "sample"],
}

MANUAL_SOURCE = {
    "id": "manual-intake",
    "name": "Manual Intake",
    "kind": "manual",
    "status": "ready",
    "url": "",
    "recipe_path": "",
    "added_at": "2026-05-09",
    "enabled": True,
    "notes": "Test fixture manual source.",
    "tags": ["manual", "fallback"],
}


def write_sample_package(
    root: Path,
    *,
    run_id: str = "run-1",
    stable_id: str = "stable-1",
    title: str = "SAP ABAP Consultant",
    run_date: date = date(2026, 5, 6),
) -> dict[str, str]:
    return write_job_package(
        Job(
            title=title,
            company="Recruiter",
            url=f"https://example.com/{stable_id}",
            application_url=f"https://example.com/{stable_id}/apply",
        ),
        MatchResult(total_score=82, category="strong", recommended_angle="Lead with ABAP", concerns=["Confirm rate"]),
        GeneratedPackage("cv", "app", "forms", "analysis", [], []),
        run_date,
        root=root,
        run_id=run_id,
        stable_id=stable_id,
        fuzzy_key=f"fuzzy-{stable_id}",
        state="new",
    )


def seed_source_registry(root: Path, *sources: dict) -> None:
    source_dir = root / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    lines = ["sources:"]
    for source in sources:
        lines.extend(
            [
                f"  - id: {source['id']}",
                f"    name: {source['name']}",
                f"    kind: {source.get('kind', 'manual')}",
                f"    status: {source.get('status', 'needs_review')}",
                f"    url: {source.get('url', '')}",
                f"    recipe_path: {source.get('recipe_path', '')}",
                f"    added_at: {source.get('added_at', '')}",
                f"    enabled: {'true' if source.get('enabled') else 'false'}",
                f"    notes: {source.get('notes', '')}",
            ]
        )
        tags = source.get("tags") or []
        if tags:
            lines.append("    tags:")
            lines.extend(f"      - {tag}" for tag in tags)
        else:
            lines.append("    tags: []")
    (source_dir / "source-registry.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def seed_eursap_source(root: Path) -> None:
    _write_recipe(root, "experimental/eursap-jobs.yaml", "Eursap Jobs (experimental)", "https://eursap.eu/jobs")
    seed_source_registry(root, EURSAP_SOURCE)


def seed_whitehall_source(root: Path) -> None:
    _write_recipe(
        root,
        "experimental/whitehall-sap-contract.yaml",
        "Whitehall Resources SAP Jobs",
        "https://www.whitehallresources.com/sap-jobs/",
    )
    seed_source_registry(root, WHITEHALL_SOURCE)


def seed_common_sources(root: Path) -> None:
    _write_recipe(root, "experimental/eursap-jobs.yaml", "Eursap Jobs (experimental)", "https://eursap.eu/jobs")
    _write_recipe(
        root,
        "experimental/whitehall-sap-contract.yaml",
        "Whitehall Resources SAP Jobs",
        "https://www.whitehallresources.com/sap-jobs/",
    )
    seed_source_registry(root, MANUAL_SOURCE, SAMPLE_SOURCE, EURSAP_SOURCE, WHITEHALL_SOURCE)


def _write_recipe(root: Path, relative: str, source_name: str, start_url: str) -> None:
    path = root / "sources" / "recipes" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"source_name: {source_name}\n"
        f"start_url: {start_url}\n"
        "mode: static_html\n"
        "listing:\n"
        "  card_selector: article\n"
        "  title_selector: h2\n"
        "  link_selector: a\n",
        encoding="utf-8",
    )
