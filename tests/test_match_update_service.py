from __future__ import annotations

from pathlib import Path

from tests.helpers import write_sample_package

from job_agent.io.json_store import read_json, write_json
from job_agent.services.ai_search_service import AiSearchEvaluation
from job_agent.services.match_update_service import MatchUpdateService
from job_agent.services.package_index_service import PackageIndexService


def test_apply_ai_matching_updates_saved_index_with_separate_and_display_scores(
    monkeypatch,
    template_project: Path,
) -> None:
    paths = write_sample_package(template_project, stable_id="stable-1")
    job_path = Path(paths["job"])
    job = read_json(job_path, {})
    job["description"] = "Strong ABAP RAP contract role with Gateway delivery."
    job["raw_text"] = job["description"]
    write_json(job_path, job)
    calls: list[str] = []

    class FakeAiSearchService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def is_configured(self) -> bool:
            return True

        def evaluate(self, *args, **kwargs) -> AiSearchEvaluation:
            calls.append(str(kwargs.get("stable_id") or ""))
            return AiSearchEvaluation(
                status="evaluated",
                summary="Strong fit.",
                recommended_angle="Lead with ABAP.",
                fit_confidence="high",
                match_score=94,
                employment_conditions={"employment_type": "contract", "remote": "hybrid"},
                should_prioritize=True,
                model="fake-model",
            )

        def failed(self, error: str) -> AiSearchEvaluation:
            return AiSearchEvaluation(status="failed", error=error)

    monkeypatch.setattr("job_agent.services.match_update_service.AiSearchService", FakeAiSearchService)

    package = PackageIndexService(template_project).find_package("stable-1")
    result = MatchUpdateService(template_project).apply_ai_matching([package])

    assert result.updated == 1
    assert result.skipped == 0
    assert result.failed == 0
    assert calls == ["stable-1"]
    index = read_json(Path(paths["index"]), {})
    assert index["ai_match_score"] == 94
    assert index["match_score"] == 88
    assert index["deterministic_match_score"] == 82
    assert index["ai_employment_conditions"]["remote"] == "hybrid"


def test_apply_ai_matching_skips_failed_evaluations_that_already_attempted_llm(
    monkeypatch,
    template_project: Path,
) -> None:
    paths = write_sample_package(template_project, stable_id="stable-1")
    index = read_json(Path(paths["index"]), {})
    index["ai_evaluation_status"] = "failed"
    index["ai_error"] = "previous timeout"
    write_json(Path(paths["index"]), index)

    class FakeAiSearchService:
        def __init__(self, root: Path) -> None:
            self.root = root

        def is_configured(self) -> bool:
            return True

        def evaluate(self, *args, **kwargs) -> AiSearchEvaluation:
            raise AssertionError("Failed AI evaluations should not be queried again")

    monkeypatch.setattr("job_agent.services.match_update_service.AiSearchService", FakeAiSearchService)

    package = PackageIndexService(template_project).find_package("stable-1")
    result = MatchUpdateService(template_project).apply_ai_matching([package])

    assert result.updated == 0
    assert result.skipped == 1
    assert result.failed == 0


def test_recalculate_deterministic_preserves_existing_ai_projection(template_project: Path) -> None:
    paths = write_sample_package(template_project, stable_id="stable-1")
    index_path = Path(paths["index"])
    index = read_json(index_path, {})
    index.update({"ai_evaluation_status": "evaluated", "ai_match_score": 94})
    write_json(index_path, index)

    package = PackageIndexService(template_project).find_package("stable-1")
    result = MatchUpdateService(template_project).recalculate_deterministic([package])

    assert result.updated == 1
    refreshed = read_json(index_path, {})
    assert refreshed["ai_match_score"] == 94
    assert refreshed["match_score"] == round((refreshed["deterministic_match_score"] + 94) / 2)
