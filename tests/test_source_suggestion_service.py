from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from job_agent.services.source_suggestion_service import SourceSuggestionService


def test_source_suggestion_prompt_uses_profile_existing_sources_and_safety_rules(project_root: Path) -> None:
    prompt = SourceSuggestionService(project_root).build_prompt("Prioritize Nordic SAP contract boards.")

    assert "SAP ABAP" in prompt
    assert "Prioritize Nordic SAP contract boards." in prompt
    assert '"existing_sources"' in prompt
    assert "must not submit applications" in prompt
    assert "bypass captcha" in prompt
    assert "Return only strict JSON" in prompt


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

    result = SourceSuggestionService(project_root).suggest_with_llm("EU contracts")

    assert result.model == "fake-sonnet"
    assert result.suggestions[0].name == "Example SAP Jobs"
    assert calls[0]["purpose"] == "source_suggestion"
    assert calls[0]["run_id"] == "manual"
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
