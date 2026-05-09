from __future__ import annotations

from pathlib import Path

from job_agent.cli import dry_run_source
from job_agent.models import Job, SourceRunResult, SourceWarning
from job_agent.services.source_dry_run_service import DryRunJobPreview, SourceDryRunResult, SourceDryRunService


class FakeResponse:
    def __init__(self, text: str, url: str = "https://example.com/jobs") -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


def test_dry_run_missing_source_returns_not_found(project_root: Path) -> None:
    result = SourceDryRunService(project_root).dry_run("missing")

    assert result.status == "not_found"
    assert result.job_count == 0


def test_dry_run_disabled_source_does_not_execute_adapter(monkeypatch, project_root: Path) -> None:
    _write_execution_source(project_root, enabled=False)

    def fail_if_called(source, root):
        raise AssertionError("adapter should not run")

    monkeypatch.setattr("job_agent.services.source_dry_run_service.adapter_for_source", fail_if_called)

    result = SourceDryRunService(project_root).dry_run("sample-source")

    assert result.status == "disabled"
    assert result.job_count == 0


def test_dry_run_force_disabled_executes_adapter(monkeypatch, project_root: Path) -> None:
    _write_execution_source(project_root, enabled=False)

    class FakeAdapter:
        def fetch(self):
            return SourceRunResult(jobs=[Job(title="SAP ABAP Consultant", source="Sample", source_id="sample-source")])

    monkeypatch.setattr("job_agent.services.source_dry_run_service.adapter_for_source", lambda source, root: FakeAdapter())

    result = SourceDryRunService(project_root).dry_run("sample-source", force_disabled=True)

    assert result.status == "success"
    assert result.forced_disabled is True
    assert result.jobs[0].source_id == "sample-source"


def test_dry_run_enabled_recipe_html_source_extracts_jobs(monkeypatch, project_root: Path) -> None:
    recipe_path = _write_recipe(project_root)
    _write_execution_source(project_root, enabled=True, recipe_path=recipe_path.relative_to(project_root).as_posix())
    html = """
    <article class="job-card">
      <a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a>
      <span class="location">Remote</span>
      <p class="description">ABAP RAP CDS contract role.</p>
    </article>
    """
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse(html))

    result = SourceDryRunService(project_root).dry_run("sample-source")

    assert result.status == "success"
    assert result.job_count == 1
    assert result.jobs[0].title == "SAP ABAP Consultant"
    assert result.jobs[0].source_id == "sample-source"
    assert result.jobs[0].location == "Remote"


def test_dry_run_collects_warnings(monkeypatch, project_root: Path) -> None:
    _write_execution_source(project_root, enabled=True)

    class FakeAdapter:
        def fetch(self):
            return SourceRunResult(
                jobs=[Job(title="SAP ABAP Consultant", source="Sample", source_id="sample-source")],
                warnings=[SourceWarning("Sample", "Check details", "https://example.com/jobs")],
            )

    monkeypatch.setattr("job_agent.services.source_dry_run_service.adapter_for_source", lambda source, root: FakeAdapter())

    result = SourceDryRunService(project_root).dry_run("sample-source")

    assert result.status == "warning"
    assert result.warning_count == 1
    assert result.warnings == ["Sample: Check details"]


def test_dry_run_does_not_write_packages_seen_state_or_runs(monkeypatch, project_root: Path) -> None:
    _write_execution_source(project_root, enabled=True)

    class FakeAdapter:
        def fetch(self):
            return SourceRunResult(jobs=[Job(title="SAP ABAP Consultant", source="Sample", source_id="sample-source")])

    monkeypatch.setattr("job_agent.services.source_dry_run_service.adapter_for_source", lambda source, root: FakeAdapter())

    result = SourceDryRunService(project_root).dry_run("sample-source")

    assert result.status == "success"
    assert not list((project_root / "output").glob("*/*/index.json"))
    assert not (project_root / "jobs" / "seen_jobs.json").exists()
    assert not (project_root / "output" / "runs" / "runs.json").exists()
    assert not list((project_root / "output" / "daily-digests").glob("*"))


def test_cli_dry_run_source_prints_key_fields_and_no_writes(monkeypatch, capsys) -> None:
    class FakeService:
        def dry_run(self, source_id, *, force_disabled=False):
            assert source_id == "sample-source"
            assert force_disabled is True
            return SourceDryRunResult(
                source_id="sample-source",
                source_name="Sample Recipe Source",
                source_type="recipe_html",
                source_enabled=False,
                forced_disabled=True,
                status="success",
                job_count=1,
                jobs=[
                    DryRunJobPreview(
                        title="SAP ABAP Consultant",
                        url="https://example.com/jobs/sap-abap",
                        source="Sample Recipe Source",
                        source_id="sample-source",
                        location="Remote",
                        description_preview="ABAP RAP CDS role.",
                    )
                ],
            )

    monkeypatch.setattr("job_agent.services.source_dry_run_service.SourceDryRunService", lambda: FakeService())

    dry_run_source("sample-source", force_disabled=True)

    output = capsys.readouterr().out
    assert "Source id: sample-source" in output
    assert "Dry-run status: success" in output
    assert "SAP ABAP Consultant" in output
    assert "Source id: sample-source" in output
    assert "No packages, seen state, materials, digests, or run records were written." in output


def _write_execution_source(
    project_root: Path,
    *,
    enabled: bool,
    recipe_path: str = "sources/recipes/test-recipe.yaml",
) -> None:
    (project_root / "sources" / "recruiting-sites.yaml").write_text(
        "sources:\n"
        "  - name: Sample Recipe Source\n"
        "    source_id: sample-source\n"
        "    type: recipe_html\n"
        "    url: https://example.com/jobs\n"
        f"    recipe_path: {recipe_path}\n"
        f"    enabled: {'true' if enabled else 'false'}\n",
        encoding="utf-8",
    )


def _write_recipe(project_root: Path) -> Path:
    recipe_path = project_root / "sources" / "recipes" / "test-recipe.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(
        "source_name: Test Recipe\n"
        "mode: static_html\n"
        "listing:\n"
        "  card_selector: article.job-card\n"
        "  title_selector: a.job-link\n"
        "  link_selector: a.job-link\n"
        "  location_selector: .location\n"
        "  description_selector: .description\n"
        "accept:\n"
        "  url_contains:\n"
        "    - /jobs/\n",
        encoding="utf-8",
    )
    return recipe_path
