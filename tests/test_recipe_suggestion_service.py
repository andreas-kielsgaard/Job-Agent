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


def test_refinement_retries_when_table_headers_contradict_fields(project_root: Path) -> None:
    artifact = _write_artifact(project_root, page_html=_accuro_like_table_page())
    client = FakeRecipeSuggestionClient(
        [
            _llm_response(_accuro_wrong_semantic_recipe_yaml()),
            _llm_response(_accuro_semantic_recipe_yaml()),
        ]
    )

    result = suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=3)

    assert result.accepted is True
    assert len(result.attempts) == 2
    assert result.attempts[0].quality_status == "poor"
    assert any("Category" in warning for warning in result.attempts[0].quality_warnings)
    assert any("Application deadline" in warning for warning in result.attempts[0].quality_warnings)
    assert result.attempts[1].quality_status in {"good", "warning"}
    assert "Previous suggested YAML" in client.prompts[1]


def test_refinement_rejects_footer_media_links_that_look_like_projects(project_root: Path) -> None:
    artifact = _write_artifact(project_root, page_html=_footer_media_project_page())
    client = FakeRecipeSuggestionClient(_llm_response(_footer_media_project_recipe_yaml()))

    result = suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=1)

    assert result.accepted is False
    assert result.attempts[0].quality_status == "poor"
    assert any("non-job URLs" in warning for warning in result.attempts[0].quality_warnings)


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


def test_refinement_accepts_deterministic_whitehall_blueprint_without_llm(project_root: Path) -> None:
    artifact = _write_whitehall_blueprint_artifact(project_root)
    client = FakeRecipeSuggestionClient("{}")

    result = suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=3)

    assert result.accepted is True
    assert client.prompts == []
    assert result.final_result.schema_valid is True
    assert "div.job-item" in result.final_result.suggested_recipe_yaml
    assert "a.page-numbers" in result.final_result.suggested_recipe_yaml
    assert "follow: true" in result.final_result.suggested_recipe_yaml
    assert result.attempts[0].quality_status == "good"


def test_refinement_rejects_non_positive_max_attempts(project_root: Path) -> None:
    artifact = _write_artifact(project_root)
    client = FakeRecipeSuggestionClient(_llm_response(VALID_RECIPE_YAML))

    try:
        suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=0)
    except ValueError as exc:
        assert "positive integer" in str(exc)
    else:
        raise AssertionError("Expected ValueError for max_attempts=0")


def test_refinement_does_not_call_llm_when_capture_has_no_job_evidence(project_root: Path) -> None:
    artifact = _write_not_recommended_artifact(project_root)
    client = FakeRecipeSuggestionClient(_llm_response(VALID_RECIPE_YAML))

    result = suggest_recipe_with_refinement(artifact, llm_client=client, max_attempts=3)

    assert client.prompts == []
    assert result.accepted is False
    assert result.final_result.selected_strategy == "not_recommended"
    assert result.final_result.schema_valid is False
    assert result.attempts[0].quality_status == "poor"
    assert "browser-rendered capture" in " ".join(result.final_result.warnings)


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


def _write_whitehall_blueprint_artifact(project_root: Path) -> Path:
    artifact = project_root / "output" / "recipe-calibration" / "whitehall"
    artifact.mkdir(parents=True, exist_ok=True)
    page_html = """
    <html><body>
      <div class="job-item">
        <span class="job-type">Contract</span>
        <h3><a href="/job/sap-eam/">SAP EAM Consultant</a></h3>
        <span class="job-location">Sweden</span>
      </div>
      <div class="job-item">
        <span class="job-type">Contract</span>
        <h3><a href="/job/sap-abap/">SAP ABAP Consultant</a></h3>
        <span class="job-location">Remote</span>
      </div>
      <a class="page-numbers" href="/sap-jobs/page/2/">2</a>
      <a class="next page-numbers" href="/sap-jobs/page/2/">Next</a>
    </body></html>
    """
    detail_html = """
    <html><body>
      <section class="job-single">
        <h1>SAP EAM Consultant</h1>
        <span class="job-location">Sweden</span>
        <span class="job-type">Contract</span>
      </section>
      <script type="application/ld+json">
        {"@type":"JobPosting","title":"SAP EAM Consultant","description":"Long SAP EAM detail description with migration, PM, integration and stakeholder delivery responsibilities across an enterprise programme."}
      </script>
    </body></html>
    """
    (artifact / "summary.md").write_text("# Whitehall\n", encoding="utf-8")
    (artifact / "visible-text.txt").write_text("SAP EAM Consultant Contract Sweden", encoding="utf-8")
    (artifact / "candidate-elements.html").write_text("", encoding="utf-8")
    (artifact / "page.html").write_text(page_html, encoding="utf-8")
    (artifact / "detail-sample.html").write_text(detail_html, encoding="utf-8")
    (artifact / "detail-visible-text.txt").write_text("SAP EAM Consultant Sweden Contract", encoding="utf-8")
    (artifact / "selector-report.json").write_text(
        json.dumps(
            {
                "url": "https://www.whitehallresources.com/sap-jobs/",
                "capture_mode": "static_html",
                "detail_sample_url": "https://www.whitehallresources.com/job/sap-eam/",
                "detail_sample_captured": True,
                "candidates": [],
                "recipe_blueprint": {
                    "confidence": "high",
                    "recipe": {
                        "source_name": "Recipe source",
                        "start_url": "https://www.whitehallresources.com/sap-jobs/",
                        "mode": "static_html",
                        "listing": {
                            "card_selector": "div.job-item",
                            "title_selector": "h3 a",
                            "link_selector": "h3 a",
                            "location_selector": ".job-location",
                            "workload_selector": ".job-type",
                        },
                        "accept": {"url_contains": ["/job/"]},
                        "detail": {
                            "follow": True,
                            "use_json_ld": True,
                            "title_selector": [".job-single h1", "h1"],
                            "location_selector": ".job-single .job-location",
                            "workload_selector": ".job-single .job-type",
                            "max_detail_pages": 5,
                            "request_delay_seconds": 1.0,
                        },
                        "pagination": {
                            "page_link_selector": "a.page-numbers",
                            "next_selector": "a.next.page-numbers",
                            "max_pages": 2,
                            "request_delay_seconds": 1.0,
                        },
                        "limits": {"max_cards": 25, "min_title_length": 8, "min_description_length": 0},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return artifact


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


def _write_not_recommended_artifact(project_root: Path) -> Path:
    artifact = project_root / "output" / "recipe-calibration" / "empty-capture"
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "summary.md").write_text("# Empty\nWarning: No job-detail link was found.\n", encoding="utf-8")
    (artifact / "visible-text.txt").write_text("Search Jobs Contact Us", encoding="utf-8")
    (artifact / "candidate-elements.html").write_text("", encoding="utf-8")
    (artifact / "page.html").write_text("<html><body>Search Jobs Contact Us</body></html>", encoding="utf-8")
    (artifact / "selector-report.json").write_text(
        json.dumps(
            {
                "url": "https://example.com/search",
                "capture_mode": "static_html",
                "candidates": [],
                "detail_sample_captured": False,
                "recipe_blueprint": {
                    "status": "not_recommended",
                    "warnings": ["No stable repeated listing card selector was found."],
                    "recipe": {},
                },
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


def _accuro_like_table_page() -> str:
    return """
    <html>
      <body>
        <table>
          <thead>
            <tr>
              <th>Position</th>
              <th>Category</th>
              <th>Location</th>
              <th>Application deadline</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><a href="/freelance_projects/senior-iam/">Senior IAM / IGA Engineer</a></td>
              <td>IT</td>
              <td>Copenhagen</td>
              <td>28 May, 2026</td>
            </tr>
            <tr>
              <td><a href="/freelance_projects/test-manager/">Test Manager</a></td>
              <td>Test Management &amp; Test</td>
              <td>København</td>
              <td>26 May, 2026</td>
            </tr>
          </tbody>
        </table>
      </body>
    </html>
    """


def _accuro_wrong_semantic_recipe_yaml() -> str:
    return """source_name: Accuro
start_url: https://accuro.dk/en/consultant/freelance-projects/
mode: static_html
listing:
  card_selector: tbody tr
  title_selector: td:nth-of-type(1) a
  link_selector: td:nth-of-type(1) a
  location_selector: td:nth-of-type(3)
  workload_selector: td:nth-of-type(2)
  posted_date_selector: td:nth-of-type(4)
accept:
  url_contains:
    - /freelance_projects/
limits:
  max_cards: 25
"""


def _accuro_semantic_recipe_yaml() -> str:
    return """source_name: Accuro
start_url: https://accuro.dk/en/consultant/freelance-projects/
mode: static_html
listing:
  card_selector: tbody tr
  title_selector: td:nth-of-type(1) a
  link_selector: td:nth-of-type(1) a
  location_selector: td:nth-of-type(3)
accept:
  url_contains:
    - /freelance_projects/
limits:
  max_cards: 25
"""


def _footer_media_project_page() -> str:
    return """
    <html>
      <body>
        <footer>
          <div class="row">
            <p>ManpowerGroup helps organizations transform in a fast-changing world of work.</p>
            <a href="/-/media/project/manpowergroup/legal/sap-project-report.pdf">SAP Project Report</a>
            <a href="/en/privacy-policy">Privacy Policy</a>
          </div>
        </footer>
      </body>
    </html>
    """


def _footer_media_project_recipe_yaml() -> str:
    return """source_name: Experis
start_url: https://www.experis.pl/en/search?page=1&searchKeyword=SAP
mode: rendered_html
listing:
  card_selector: div.row
  title_selector: a[href*="/project/"]
  link_selector: a[href*="/project/"]
accept:
  url_contains:
    - /project/
limits:
  max_cards: 25
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
