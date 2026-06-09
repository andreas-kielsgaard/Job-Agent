from __future__ import annotations

from datetime import date
from pathlib import Path

from job_agent.models import Job, MatchResult
from job_agent.services.ai_search_service import (
    AiSearchEvaluation,
    AiSearchService,
    parse_ai_search_response,
    should_ai_evaluate_job,
)
from job_agent.llm import LlmCompletion


def test_should_ai_evaluate_promising_and_skip_excluded() -> None:
    profile = {"thresholds": {"ai_evaluation_score": 60}}

    assert should_ai_evaluate_job(Job(title="SAP ABAP"), MatchResult(80, "strong"), profile, [])
    assert should_ai_evaluate_job(Job(title="SAP Fullstack Developer"), MatchResult(50, "weak"), profile, [])
    assert not should_ai_evaluate_job(Job(title="Old SAP Role"), MatchResult(0, "excluded"), profile, ["highlight"])
    assert not should_ai_evaluate_job(Job(title="Low fit"), MatchResult(20, "weak"), profile, [])


def test_parse_valid_structured_response() -> None:
    evaluation = parse_ai_search_response(
        """
        {
          "summary": "Strong ABAP/Gateway fit with one caveat.",
          "recommended_angle": "Lead with ABAP and Gateway.",
          "fit_confidence": "high",
          "risk_flags": ["Confirm Fiori depth"],
          "key_profile_evidence": ["ABAP", "OData"],
          "should_prioritize": true
        }
        """
    )

    assert evaluation.summary.startswith("Strong ABAP")
    assert evaluation.fit_confidence == "high"
    assert evaluation.risk_flags == ["Confirm Fiori depth"]
    assert evaluation.should_prioritize is True


def test_parse_malformed_response_keeps_summary_for_review() -> None:
    evaluation = parse_ai_search_response("Useful but not JSON")

    assert evaluation.summary == "Useful but not JSON"
    assert evaluation.fit_confidence == "medium"
    assert evaluation.risk_flags == ["AI response was not valid JSON."]


def test_ai_search_service_uses_llm_service(monkeypatch, template_project: Path) -> None:
    calls = []

    class FakeLlmService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def is_configured(self) -> bool:
            return True

        def complete(self, prompt: str, **kwargs) -> LlmCompletion:
            calls.append({"prompt": prompt, **kwargs})
            return LlmCompletion(
                text='{"summary":"Good fit","recommended_angle":"Lead with ABAP","fit_confidence":"high","risk_flags":[],"key_profile_evidence":["ABAP"],"should_prioritize":true}',
                model="fake-model",
            )

    monkeypatch.setattr("job_agent.services.ai_search_service.LlmService", FakeLlmService)

    evaluation = AiSearchService(template_project).evaluate(
        Job(title="SAP ABAP Consultant"),
        MatchResult(82, "strong"),
        {"canonical_cv": "CV", "experience": []},
        ["strong match category"],
        run_id="run-1",
        stable_id="stable-1",
    )

    assert isinstance(evaluation, AiSearchEvaluation)
    assert evaluation.status == "evaluated"
    assert evaluation.model == "fake-model"
    assert evaluation.should_prioritize is True
    assert calls[0]["purpose"] == "ai_search_evaluation"
    assert calls[0]["run_id"] == "run-1"
    assert calls[0]["associated_job_id"] == "stable-1"


def test_ai_search_prompt_handles_yaml_date_values(monkeypatch, template_project: Path) -> None:
    class FakeLlmService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def is_configured(self) -> bool:
            return True

        def complete(self, prompt: str, **kwargs) -> LlmCompletion:
            assert "2026-05-07" in prompt
            return LlmCompletion(
                text='{"summary":"Good fit","recommended_angle":"Lead with ABAP","fit_confidence":"high","risk_flags":[],"key_profile_evidence":["ABAP"],"should_prioritize":true}',
                model="fake-model",
            )

    monkeypatch.setattr("job_agent.services.ai_search_service.LlmService", FakeLlmService)

    evaluation = AiSearchService(template_project).evaluate(
        Job(title="SAP ABAP Consultant", posted_date=date(2026, 5, 7)),
        MatchResult(82, "strong"),
        {"canonical_cv": "CV", "experience": []},
        ["strong match category"],
        run_id="run-1",
        stable_id="stable-1",
    )

    assert evaluation.status == "evaluated"
