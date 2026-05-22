from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient


def test_recipe_editor_renders_recipe_and_snapshot_picker(client: TestClient, project_root: Path) -> None:
    recipe_path, artifact_dir = _write_recipe_and_artifact(project_root)

    response = client.get(f"/recipe-editor?recipe_path={recipe_path}&artifact_dir={artifact_dir}")

    assert response.status_code == 200
    assert "Recipe Editor" in response.text
    assert "Synthetic Board" in response.text
    assert "In-Page View" in response.text
    assert "selector__listing__title_selector" in response.text
    assert "/recipe-editor/snapshot" in response.text

    snapshot = client.get(f"/recipe-editor/snapshot?artifact_dir={artifact_dir}")

    assert snapshot.status_code == 200
    assert "recipe-editor-selection" in snapshot.text
    assert "SAP Basis Consultant" in snapshot.text


def test_recipe_editor_saves_selector_fields(client: TestClient, project_root: Path) -> None:
    recipe_path, artifact_dir = _write_recipe_and_artifact(project_root)

    response = client.post(
        "/recipe-editor/save",
        data={
            "recipe_path": recipe_path,
            "artifact_dir": artifact_dir,
            "selector__listing__card_selector": "article.vacancy",
            "selector__listing__title_selector": "h2.title\nh3.alt-title",
            "selector__listing__link_selector": "a.detail",
            "selector__listing__location_selector": ".place",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/recipe-editor?")
    data = yaml.safe_load((project_root / recipe_path).read_text(encoding="utf-8"))
    assert data["listing"]["card_selector"] == "article.vacancy"
    assert data["listing"]["title_selector"] == ["h2.title", "h3.alt-title"]
    assert data["listing"]["link_selector"] == "a.detail"
    assert data["listing"]["location_selector"] == ".place"
    assert data["source_name"] == "Synthetic Board"


def _write_recipe_and_artifact(project_root: Path) -> tuple[str, str]:
    recipe_path = Path("sources/recipes/experimental/synthetic.yaml")
    full_recipe_path = project_root / recipe_path
    full_recipe_path.parent.mkdir(parents=True, exist_ok=True)
    full_recipe_path.write_text(
        "source_name: Synthetic Board\n"
        "start_url: https://example.com/jobs\n"
        "mode: static_html\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: h2\n"
        "  link_selector: a\n"
        "detail:\n"
        "  follow: false\n",
        encoding="utf-8",
    )
    artifact_dir = Path("output/recipe-calibration/synthetic")
    full_artifact_dir = project_root / artifact_dir
    full_artifact_dir.mkdir(parents=True, exist_ok=True)
    (full_artifact_dir / "page.html").write_text(
        "<html><body><article class='job-card'><h2>SAP Basis Consultant</h2><a href='/jobs/1'>Open</a></article></body></html>",
        encoding="utf-8",
    )
    (full_artifact_dir / "selector-report.json").write_text(
        '{"url":"https://example.com/jobs","capture_mode":"static_html","candidates":[{"selector":"article.job-card"}]}',
        encoding="utf-8",
    )
    return recipe_path.as_posix(), artifact_dir.as_posix()
