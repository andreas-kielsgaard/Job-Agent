from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.cli import approve_recipe_candidate
from job_agent.services.recipe_candidate_approval_service import RecipeCandidateApprovalService
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_suggestion_service import RecipeSuggestionResult
from job_agent.services.source_health_service import SourceHealthService

VALID_RECIPE_YAML = """source_name: Example Jobs
start_url: https://example.com/jobs
mode: static_html
listing:
  card_selector: article.job-card
  title_selector: a.job-link
  link_selector: a.job-link
  description_selector: .description
accept:
  url_contains:
    - /jobs/
limits:
  max_cards: 10
"""


def test_approving_pending_candidate_writes_recipe_path(project_root: Path) -> None:
    candidate = _save_candidate(project_root)

    result = RecipeCandidateApprovalService(project_root).approve(
        candidate.candidate_id,
        "sources/recipes/experimental/example.yaml",
        source_id="example-source",
    )

    recipe_path = project_root / "sources" / "recipes" / "experimental" / "example.yaml"
    assert recipe_path.read_text(encoding="utf-8").startswith("source_name: Example Jobs")
    assert result.preview is not None
    assert result.preview.extracted_job_count == 1


def test_approval_refuses_rejected_candidate(project_root: Path) -> None:
    store = RecipeCandidateStore(project_root)
    candidate = _save_candidate(project_root)
    store.reject_candidate(candidate.candidate_id, reason="No")

    with pytest.raises(ValueError, match="Only pending"):
        RecipeCandidateApprovalService(project_root).approve(
            candidate.candidate_id,
            "sources/recipes/experimental/example.yaml",
        )


def test_approval_refuses_already_approved_candidate(project_root: Path) -> None:
    candidate = _save_candidate(project_root)
    service = RecipeCandidateApprovalService(project_root)
    service.approve(candidate.candidate_id, "sources/recipes/experimental/example.yaml")

    with pytest.raises(ValueError, match="Only pending"):
        service.approve(candidate.candidate_id, "sources/recipes/experimental/example-2.yaml")


def test_approval_refuses_overwrite_without_flag(project_root: Path) -> None:
    candidate = _save_candidate(project_root)
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "example.yaml"
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text("keep me\n", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        RecipeCandidateApprovalService(project_root).approve(
            candidate.candidate_id, "sources/recipes/experimental/example.yaml"
        )

    assert recipe_path.read_text(encoding="utf-8") == "keep me\n"


def test_approval_allows_overwrite_when_explicit(project_root: Path) -> None:
    candidate = _save_candidate(project_root)
    recipe_path = project_root / "sources" / "recipes" / "experimental" / "example.yaml"
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text("old\n", encoding="utf-8")

    RecipeCandidateApprovalService(project_root).approve(
        candidate.candidate_id,
        "sources/recipes/experimental/example.yaml",
        overwrite=True,
    )

    assert recipe_path.read_text(encoding="utf-8").startswith("source_name: Example Jobs")


def test_approval_validates_recipe_path_under_sources_recipes(project_root: Path) -> None:
    candidate = _save_candidate(project_root)

    with pytest.raises(ValueError, match="sources/recipes"):
        RecipeCandidateApprovalService(project_root).approve(candidate.candidate_id, "sources/recruiting-sites.yaml")


def test_approval_saves_source_health_and_candidate_metadata(project_root: Path) -> None:
    candidate = _save_candidate(project_root)

    result = RecipeCandidateApprovalService(project_root).approve(
        candidate.candidate_id,
        "sources/recipes/experimental/example.yaml",
        source_id="example-source",
    )
    approved = RecipeCandidateStore(project_root).load_candidate(candidate.candidate_id)
    health = SourceHealthService(project_root).get_health("example-source")

    assert result.health_record is not None
    assert approved.status == "approved"
    assert approved.approved_recipe_path == "sources/recipes/experimental/example.yaml"
    assert approved.approved_source_id == "example-source"
    assert approved.preview_saved is True
    assert approved.preview_extracted_job_count == 1
    assert health.extracted_job_count == 1
    assert health.health_status == "good"


def test_missing_page_html_fails_approval_cleanly(project_root: Path) -> None:
    candidate = _save_candidate(project_root)
    (project_root / "output" / "recipe-calibration" / "example" / "page.html").unlink()

    with pytest.raises(ValueError, match="page.html"):
        RecipeCandidateApprovalService(project_root).approve(
            candidate.candidate_id,
            "sources/recipes/experimental/example.yaml",
            source_id="example-source",
        )

    assert not (project_root / "sources" / "recipes" / "experimental" / "example.yaml").exists()
    assert RecipeCandidateStore(project_root).load_candidate(candidate.candidate_id).status == "pending"
    assert SourceHealthService(project_root).get_health("example-source").health_status == "untested"


def test_cli_approve_recipe_candidate_prints_summary(capsys, project_root: Path) -> None:
    candidate = _save_candidate(project_root)

    approve_recipe_candidate(
        candidate.candidate_id,
        recipe_path="sources/recipes/experimental/example.yaml",
        source_id="example-source",
        root=project_root,
    )

    output = capsys.readouterr().out
    assert "Recipe candidate approved" in output
    assert "Approved recipe path: sources/recipes/experimental/example.yaml" in output
    assert "Jobs extracted: 1" in output
    assert "Source health saved: True" in output
    assert "Source execution was not enabled" in output


def _save_candidate(project_root: Path):
    artifact = project_root / "output" / "recipe-calibration" / "example"
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "page.html").write_text(
        """
        <article class="job-card">
          <a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a>
          <p class="description">SAP ABAP contract role supporting an S/4HANA programme with integrations.</p>
        </article>
        """,
        encoding="utf-8",
    )
    result = RecipeSuggestionResult(
        source_name="Example Jobs",
        start_url="https://example.com/jobs",
        artifact_dir=artifact,
        suggested_recipe_yaml=VALID_RECIPE_YAML,
        schema_valid=True,
    )
    return RecipeCandidateStore(project_root).save_candidate_from_suggestion(result)
