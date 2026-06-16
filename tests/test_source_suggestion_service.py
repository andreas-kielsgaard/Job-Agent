from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from job_agent.services.source_suggestion_service import SourceSuggestion, SourceSuggestionService


def test_source_suggestion_prompt_uses_profile_existing_sources_and_safety_rules(project_root: Path) -> None:
    profile = project_root / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "contact.yaml").write_text("contact:\n  title: Data Platform Lead\n", encoding="utf-8")
    (profile / "skills.yaml").write_text(
        "skills:\n  strongest:\n    - Python\n    - Analytics engineering\n",
        encoding="utf-8",
    )
    prompt = SourceSuggestionService(project_root).build_prompt("Prioritize Nordic contract boards.")

    assert "Data Platform Lead" in prompt
    assert "Analytics engineering" in prompt
    assert "Prioritize Nordic contract boards." in prompt
    assert '"existing_sources"' in prompt
    assert "must not submit applications" in prompt
    assert "bypass captcha" in prompt
    assert "Return only strict JSON" in prompt
    assert "Prefer broad source URLs" in prompt
    assert '"availability"' not in prompt
    assert '"location_policy"' not in prompt
    assert '"match_engine"' not in prompt
    assert '"disqualified_domains"' in prompt
    assert '"suggested_filters": ["Contract", "Role family"]' in prompt


def test_source_suggestion_prompt_has_no_fixed_sap_language_for_empty_profile(tmp_path: Path) -> None:
    prompt = SourceSuggestionService(tmp_path).build_prompt("")

    assert "SAP" not in prompt
    assert "ABAP" not in prompt
    assert "freelance" not in prompt.lower()
    assert "profile-derived role" in prompt


def test_source_suggestion_parser_accepts_fenced_json_response(project_root: Path) -> None:
    response = """```json
{
  "sources": [
    {
      "name": "Nordic SAP Contracts",
      "homepage_url": "https://example.com",
      "recommended_listing_url": "https://example.com/jobs?keyword=SAP",
      "why_relevant": "SAP contract board",
      "expected_signal": "ABAP and RAP freelance roles",
      "visit_instructions": "Open Jobs, search SAP ABAP, then choose Contract.",
      "suggested_filters": ["Contract", "SAP ABAP"],
      "search_terms": ["SAP RAP freelance"],
      "caveats": "Check whether filters remain in the URL.",
      "priority": 1
    }
  ]
}
```"""

    suggestions = SourceSuggestionService(project_root).parse_response(response)

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.name == "Nordic SAP Contracts"
    assert suggestion.source_url == "https://example.com/jobs?keyword=SAP"
    assert suggestion.priority_label == "High"
    assert "Suggested filters: Contract, SAP ABAP" in suggestion.notes_for_source


def test_source_suggestion_parser_filters_discontinued_and_disqualified_domains(project_root: Path) -> None:
    response = {
        "sources": [
            {
                "name": "GitHub Jobs",
                "homepage_url": "https://jobs.github.com",
                "recommended_listing_url": "https://jobs.github.com/positions",
            },
            {
                "name": "Indeed",
                "homepage_url": "https://www.indeed.com",
                "recommended_listing_url": "https://www.indeed.com/jobs?q=sap",
            },
            {
                "name": "Useful Board",
                "homepage_url": "https://useful.example",
                "recommended_listing_url": "https://useful.example/jobs",
            },
        ]
    }

    parsed = SourceSuggestionService(project_root).parse_response_with_disqualifications(json.dumps(response))

    assert [suggestion.name for suggestion in parsed.suggestions] == ["Useful Board"]
    assert {record.domain for record in parsed.disqualified} >= {"jobs.github.com", "indeed.com"}


def test_source_suggestion_service_marks_domain_overlap_as_existing(project_root: Path) -> None:
    from job_agent.services.source_registry_service import SourceRegistryService

    existing = SourceRegistryService(project_root).add_source(
        name="LinkedIn Recruiter Posts",
        url="https://www.linkedin.com/jobs/search",
    )
    suggestion = SourceSuggestionService(project_root).annotate_existing(
        [
            SourceSuggestion(
                name="LinkedIn Nordic Region",
                homepage_url="https://linkedin.com",
                recommended_listing_url="https://dk.linkedin.com/jobs",
            )
        ]
    )[0]

    assert suggestion.existing_source_id == existing.id
    assert suggestion.existing_source_name == existing.name


def test_source_suggestion_parser_repairs_invalid_json_response(
    project_root: Path,
) -> None:
    broken = (
        '{"sources":[{"name":"Example Jobs",'
        '"homepage_url":"https://example.com",'
        '"recommended_listing_url":"https://example.com/jobs" '
        '"why_relevant":"Useful broad board"}]}'
    )

    suggestions = SourceSuggestionService(project_root).parse_response(
        broken,
        repair_callback=lambda raw, error: (
            '{"sources":[{"name":"Example Jobs",'
            '"homepage_url":"https://example.com",'
            '"recommended_listing_url":"https://example.com/jobs",'
            '"why_relevant":"Useful broad board"}]}'
        ),
    )

    assert suggestions[0].name == "Example Jobs"


def test_source_suggestion_llm_generation_uses_gateway(monkeypatch, project_root: Path) -> None:
    calls = []

    class FakeLlmService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def is_configured(self) -> bool:
            return True

        def complete(self, prompt: str, **kwargs):
            calls.append({"prompt": prompt, **kwargs})
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "sources": [
                            {
                                "name": "Example SAP Jobs",
                                "homepage_url": "https://example.com",
                                "recommended_listing_url": "https://example.com/jobs",
                                "why_relevant": "Relevant SAP jobs",
                                "priority": 2,
                            }
                        ]
                    }
                ),
                model="fake-sonnet",
            )

    monkeypatch.setattr("job_agent.services.source_suggestion_service.LlmService", FakeLlmService)

    result = SourceSuggestionService(project_root).suggest_with_llm("EU contracts", llm_model="claude-opus-4-8")

    assert result.model == "fake-sonnet"
    assert result.suggestions[0].name == "Example SAP Jobs"
    assert calls[0]["purpose"] == "source_suggestion"
    assert calls[0]["run_id"] == "manual"
    assert calls[0]["model"] == "claude-opus-4-8"
    assert "EU contracts" in calls[0]["prompt"]


def test_source_suggestion_external_agent_round_trip(project_root: Path) -> None:
    service = SourceSuggestionService(project_root)

    interaction = service.prepare_external("EU contract boards")
    result = service.apply_external_response(
        interaction.interaction_id,
        json.dumps(
            {
                "sources": [
                    {
                        "name": "External SAP Jobs",
                        "homepage_url": "https://example.com",
                        "recommended_listing_url": "https://example.com/sap",
                        "why_relevant": "External agent result",
                    }
                ]
            }
        ),
    )

    assert result.model == "external-agent"
    assert result.focus == "EU contract boards"
    assert result.suggestions[0].name == "External SAP Jobs"
    assert "EU contract boards" in result.prompt
