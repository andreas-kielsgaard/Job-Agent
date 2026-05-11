from __future__ import annotations

import json
from pathlib import Path

from job_agent.cli import suggest_recipe
from job_agent.services.recipe_suggestion_service import (
    RecipeRefinementAttempt,
    RecipeRefinementResult,
    RecipeSuggestionResult,
    build_recipe_suggestion_prompt,
    load_recipe_suggestion_evidence,
    suggest_recipe_from_artifact,
    suggest_recipe_with_refinement,
)


VALID_RECIPE_YAML = """source_name: Example Jobs
start_url: https://example.com/jobs
mode: static_html
listing:
  card_selector: article.job-card
  title_selector: a.job-link
  link_selector: a.job-link
  location_selector: .location
  description_selector: .description
accept:
  url_contains:
    - /jobs/
limits:
  max_cards: 10
"""


class FakeRecipeSuggestionClient:
    def __init__(self, response: str | list[str]) -> None:
        self.responses = response if isinstance(response, list) else [response]
        self.prompts: list[str] = []

    def suggest(self, prompt: str) -> str:
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self.responses) - 1)
        return self.responses[index]


def test_evidence_loader_handles_complete_artifact_folder(project_root: Path) -> None:
    artifact = _write_artifact(project_root)

    evidence = load_recipe_suggestion_evidence(artifact, source_name="Example Jobs")

    assert evidence.source_name == "Example Jobs"
    assert evidence.start_url == "https://example.com/jobs"
    assert not evidence.warnings
    assert "article.job-card" in evidence.evidence_summary
    assert evidence.prompt_payload["top_candidates"][0]["selector"] == "article.job-card"
    assert "SAP ABAP Consultant" in evidence.prompt_payload["visible_text_sample"]


def test_evidence_loader_handles_missing_files_with_warnings(project_root: Path) -> None:
    artifact = project_root / "output" / "recipe-calibration" / "partial"
    artifact.mkdir(parents=True)
    (artifact / "visible-text.txt").write_text("SAP ABAP Consultant", encoding="utf-8")

    evidence = load_recipe_suggestion_evidence(artifact)

    assert evidence.warnings
    assert any("summary.md" in warning for warning in evidence.warnings)
    assert "SAP ABAP Consultant" in evidence.prompt_payload["visible_text_sample"]


def test_prompt_includes_candidate_selectors_and_visible_text(project_root: Path) -> None:
    evidence = load_recipe_suggestion_evidence(_write_artifact(project_root))

    prompt = build_recipe_suggestion_prompt(evidence)

    assert "article.job-card" in prompt
    assert "SAP ABAP Consultant" in prompt
    assert "Return only strict JSON" in prompt
    assert "Do not include Python code" in prompt


def test_fake_llm_response_produces_valid_result(project_root: Path) -> None:
    client = FakeRecipeSuggestionClient(_llm_response(VALID_RECIPE_YAML))

    result = suggest_recipe_from_artifact(_write_artifact(project_root), llm_client=client)

    assert result.schema_valid is True
    assert result.selected_strategy == "selector_based"
    assert result.confidence == "high"
    assert "article.job-card" in client.prompts[0]
    assert not result.validation_errors


def test_refinement_accepts_first_valid_high_quality_suggestion(project_root: Path) -> None:
    artifact = _write_artifact(project_root, page_html=_job_card_page())
    client = FakeRecipeSuggestionClient(_llm_response(VALID_RECIPE_YAML))

    result = suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=3)

    assert result.accepted is True
    assert len(result.attempts) == 1
    assert len(client.prompts) == 1
    assert result.attempts[0].schema_valid is True
    assert result.attempts[0].quality_status == "good"
    assert result.attempts[0].extracted_job_count == 1
    assert result.attempts[0].useful_titles == 1
    assert result.attempts[0].unique_urls == 1


def test_refinement_retries_after_schema_invalid_yaml(project_root: Path) -> None:
    artifact = _write_artifact(project_root, page_html=_job_card_page())
    client = FakeRecipeSuggestionClient(
        [
            _llm_response("source_name: Broken\nlisting: {}\n"),
            _llm_response(VALID_RECIPE_YAML),
        ]
    )

    result = suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=3)

    assert result.accepted is True
    assert len(result.attempts) == 2
    assert result.attempts[0].schema_valid is False
    assert "Schema validation failed" in result.attempts[0].revision_reason
    assert "Previous suggested YAML" in client.prompts[1]
    assert result.attempts[1].quality_status == "good"


def test_refinement_retries_after_schema_valid_poor_extraction(project_root: Path) -> None:
    artifact = _write_artifact(project_root, page_html=_job_card_page())
    client = FakeRecipeSuggestionClient(
        [
            _llm_response(_wrong_selector_recipe_yaml()),
            _llm_response(VALID_RECIPE_YAML),
        ]
    )

    result = suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=3)

    assert result.accepted is True
    assert len(result.attempts) == 2
    assert result.attempts[0].schema_valid is True
    assert result.attempts[0].quality_status == "poor"
    assert result.attempts[0].extracted_job_count == 0
    assert any("No jobs" in warning for warning in result.attempts[0].quality_warnings)
    assert result.attempts[1].quality_status == "good"


def test_refinement_stops_at_max_attempts_when_still_poor(project_root: Path) -> None:
    artifact = _write_artifact(project_root, page_html=_job_card_page())
    client = FakeRecipeSuggestionClient(_llm_response(_wrong_selector_recipe_yaml()))

    result = suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=2)

    assert result.accepted is False
    assert len(result.attempts) == 2
    assert len(client.prompts) == 2
    assert all(attempt.quality_status == "poor" for attempt in result.attempts)


def test_refinement_handles_missing_page_html_as_local_quality_failure(project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    (artifact / "page.html").unlink()
    client = FakeRecipeSuggestionClient(_llm_response(VALID_RECIPE_YAML))

    result = suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=1)

    assert result.accepted is False
    assert result.attempts[0].schema_valid is True
    assert result.attempts[0].quality_status == "poor"
    assert any("page.html" in warning for warning in result.attempts[0].quality_warnings)


def test_refinement_rejects_non_positive_max_attempts(project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    client = FakeRecipeSuggestionClient(_llm_response(VALID_RECIPE_YAML))

    try:
        suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=0)
    except ValueError as exc:
        assert "positive integer" in str(exc)
    else:
        raise AssertionError("Expected ValueError for max_attempts=0")


def test_invalid_suggested_recipe_yaml_returns_validation_errors(project_root: Path) -> None:
    client = FakeRecipeSuggestionClient(_llm_response("source_name: Broken\nlisting: {}\n"))

    result = suggest_recipe_from_artifact(_write_artifact(project_root), llm_client=client)

    assert result.schema_valid is False
    assert result.validation_errors
    assert "missing required listing selector" in result.validation_errors[0]


def test_cli_suggest_recipe_prints_suggestion(monkeypatch, capsys, project_root: Path) -> None:
    artifact = _write_artifact(project_root)

    monkeypatch.setattr(
        "job_agent.services.recipe_suggestion_service.suggest_recipe_from_artifact",
        lambda *args, **kwargs: _result(artifact),
    )

    suggest_recipe(str(artifact), source_name="Example Jobs")

    output = capsys.readouterr().out
    assert "Selected strategy: selector_based" in output
    assert "Schema valid: True" in output
    assert "Suggested recipe YAML:" in output
    assert "article.job-card" in output


def test_cli_suggest_recipe_output_writes_yaml(monkeypatch, capsys, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    output_path = project_root / "suggested" / "recipe.yaml"
    monkeypatch.setattr(
        "job_agent.services.recipe_suggestion_service.suggest_recipe_from_artifact",
        lambda *args, **kwargs: _result(artifact),
    )

    suggest_recipe(str(artifact), output=str(output_path))

    assert output_path.read_text(encoding="utf-8").startswith("source_name: Example Jobs")
    assert "Suggested recipe written" in capsys.readouterr().out


def test_cli_refuses_to_overwrite_without_flag(monkeypatch, capsys, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    output_path = project_root / "recipe.yaml"
    output_path.write_text("keep me\n", encoding="utf-8")
    called = False

    def fake(*args, **kwargs):
        nonlocal called
        called = True
        return _result(artifact)

    monkeypatch.setattr("job_agent.services.recipe_suggestion_service.suggest_recipe_from_artifact", fake)

    suggest_recipe(str(artifact), output=str(output_path))

    assert output_path.read_text(encoding="utf-8") == "keep me\n"
    assert called is False
    assert "Output already exists" in capsys.readouterr().out


def test_cli_overwrites_output_only_with_flag(monkeypatch, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    output_path = project_root / "recipe.yaml"
    output_path.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(
        "job_agent.services.recipe_suggestion_service.suggest_recipe_from_artifact",
        lambda *args, **kwargs: _result(artifact),
    )

    suggest_recipe(str(artifact), output=str(output_path), overwrite=True)

    assert output_path.read_text(encoding="utf-8").startswith("source_name: Example Jobs")


def test_cli_refine_prints_attempt_history_and_writes_final_yaml(monkeypatch, capsys, project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    output_path = project_root / "suggested" / "refined.yaml"
    refinement = RecipeRefinementResult(
        final_result=_result(artifact),
        attempts=[
            RecipeRefinementAttempt(
                attempt_number=1,
                suggested_recipe_yaml=_wrong_selector_recipe_yaml(),
                schema_valid=True,
                validation_errors=[],
                quality_status="poor",
                quality_warnings=["No jobs were extracted from local page.html."],
                revision_reason="Recipe extracted no jobs from local page.html.",
            ),
            RecipeRefinementAttempt(
                attempt_number=2,
                suggested_recipe_yaml=VALID_RECIPE_YAML,
                schema_valid=True,
                validation_errors=[],
                quality_status="good",
                quality_warnings=[],
                extracted_job_count=1,
                useful_titles=1,
                unique_urls=1,
                average_description_length=100,
            ),
        ],
        accepted=True,
    )

    monkeypatch.setattr(
        "job_agent.services.recipe_suggestion_service.suggest_recipe_with_refinement",
        lambda *args, **kwargs: refinement,
    )

    suggest_recipe(str(artifact), output=str(output_path), refine=True, max_attempts=3)

    output = capsys.readouterr().out
    assert "Refinement attempts: 2" in output
    assert "Attempt 1" in output
    assert "Quality warning: No jobs were extracted" in output
    assert "Attempt 2" in output
    assert output_path.read_text(encoding="utf-8").startswith("source_name: Example Jobs")


def test_cli_reports_unavailable_llm(monkeypatch, capsys, project_root: Path) -> None:
    artifact = _write_artifact(project_root)

    def unavailable(*args, **kwargs):
        raise RuntimeError("ANTHROPIC_API_KEY is missing or placeholder.")

    monkeypatch.setattr("job_agent.services.recipe_suggestion_service.suggest_recipe_from_artifact", unavailable)

    suggest_recipe(str(artifact))

    assert "Recipe suggestion unavailable" in capsys.readouterr().out


def _write_artifact(project_root: Path, page_html: str = "<html><body>large page</body></html>") -> Path:
    artifact = project_root / "output" / "recipe-calibration" / "example"
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "summary.md").write_text("# Summary\nURL: https://example.com/jobs\n", encoding="utf-8")
    (artifact / "visible-text.txt").write_text(
        "SAP ABAP Consultant Remote Contract View Job Apply Now",
        encoding="utf-8",
    )
    (artifact / "candidate-elements.html").write_text(
        '<article class="job-card"><a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a></article>',
        encoding="utf-8",
    )
    (artifact / "page.html").write_text(page_html, encoding="utf-8")
    (artifact / "selector-report.json").write_text(
        json.dumps(
            {
                "url": "https://example.com/jobs",
                "capture_mode": "static_html",
                "candidates": [
                    {
                        "selector": "article.job-card",
                        "kind": "card",
                        "text_preview": "SAP ABAP Consultant Remote Contract",
                        "contains_sap_terms": True,
                        "likely_noise": False,
                        "links": [{"text": "SAP ABAP Consultant", "href": "/jobs/sap-abap"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return artifact


def _llm_response(recipe_yaml: str) -> str:
    return json.dumps(
        {
            "suggested_recipe_yaml": recipe_yaml,
            "explanation": "Use repeated job cards and reject apply links.",
            "confidence": "high",
            "assumptions": ["Cards are stable."],
            "warnings": [],
            "selected_strategy": "selector_based",
        }
    )


def _job_card_page() -> str:
    return """
    <html>
      <body>
        <article class="job-card">
          <a class="job-link" href="/jobs/sap-abap">SAP ABAP Consultant</a>
          <span class="location">Remote</span>
          <p class="description">SAP ABAP contract role supporting an S/4HANA programme with integration work.</p>
        </article>
      </body>
    </html>
    """


def _wrong_selector_recipe_yaml() -> str:
    return """source_name: Example Jobs
start_url: https://example.com/jobs
mode: static_html
listing:
  card_selector: article.missing-card
  title_selector: a.job-link
  link_selector: a.job-link
  description_selector: .description
accept:
  url_contains:
    - /jobs/
limits:
  max_cards: 10
"""


def _result(artifact: Path) -> RecipeSuggestionResult:
    return RecipeSuggestionResult(
        source_name="Example Jobs",
        start_url="https://example.com/jobs",
        artifact_dir=artifact,
        suggested_recipe_yaml=VALID_RECIPE_YAML,
        explanation="Use cards.",
        confidence="high",
        evidence_summary="candidate selectors: article.job-card",
        selected_strategy="selector_based",
        schema_valid=True,
    )
