from __future__ import annotations

from job_agent.web.view_models.runs import build_package_triage, build_source_progress, build_triage_packages


def test_source_progress_started_completed_and_summary() -> None:
    progress = build_source_progress(
        [
            _event("source_started", source_index=1, source_count=2, source="Local YAML"),
            _event(
                "source_completed",
                source_index=1,
                source_count=2,
                source="Local YAML",
                jobs_found=3,
                warnings_count=0,
                elapsed_time_seconds=0.25,
            ),
        ]
    )

    items = progress["items"]
    assert items[0]["status"] == "completed"
    assert items[0]["jobs_found"] == 3
    assert items[0]["elapsed_time_seconds"] == 0.25
    assert items[1]["status"] == "waiting"
    assert items[1]["source_name"] == "Waiting source 2/2"
    assert progress["summary"]["total_sources"] == 2
    assert progress["summary"]["sources_completed"] == 1
    assert progress["summary"]["jobs_found_so_far"] == 3


def test_source_warning_updates_message_and_terminal_completed_status() -> None:
    progress = build_source_progress(
        [
            _event("source_started", source_index=1, source_count=1, source="HTML"),
            _event("source_warning", source_index=1, source_count=1, source="HTML", warnings_count=1),
            _event("source_completed", source_index=1, source_count=1, source="HTML", warnings_count=1, message=""),
        ]
    )

    item = progress["items"][0]
    assert item["status"] == "warning"
    assert item["warnings_count"] == 1
    assert item["latest_message"] == "0 listing(s) seen; preparing review decisions."
    assert progress["summary"]["sources_completed"] == 1
    assert progress["summary"]["warnings_so_far"] == 1


def test_source_failed_is_terminal_and_counted() -> None:
    progress = build_source_progress(
        [
            _event("source_started", source_index=1, source_count=1, source="Broken"),
            _event("source_failed", source_index=1, source_count=1, source="Broken", warnings_count=1),
        ]
    )

    item = progress["items"][0]
    assert item["status"] == "failed"
    assert item["finished_at"]
    assert progress["summary"]["sources_failed"] == 1
    assert progress["summary"]["sources_running"] == 0


def test_source_progress_current_source_uses_running_item() -> None:
    progress = build_source_progress(
        [
            _event("source_started", source_index=1, source_count=2, source="Done"),
            _event("source_completed", source_index=1, source_count=2, source="Done"),
            _event("source_started", source_index=2, source_count=2, source="Running"),
        ]
    )

    assert progress["summary"]["current_source"] == "Running"
    assert progress["summary"]["sources_running"] == 1


def test_triage_sort_prioritizes_ai_and_strong_matches() -> None:
    packages = build_triage_packages(
        [
            _package("weak old", score=92, category="weak", ai_should_prioritize=False),
            _package("strong new", score=70, category="strong", ai_should_prioritize=False),
            _package("ai priority", score=65, category="exploratory", ai_should_prioritize=True),
        ]
    )

    assert [package["title"] for package in packages] == ["ai priority", "strong new", "weak old"]


def test_triage_badges_cover_priority_match_remote_and_material_status() -> None:
    triage = build_package_triage(
        _package(
            "SAP ABAP",
            category="strong",
            ai_should_prioritize=True,
            ai_fit_confidence="high",
            remote="Fully remote",
            material_status="missing",
        )
    )

    labels = {badge["label"] for badge in triage["triage_badges"]}
    assert {"Prioritize", "Strong match", "AI high confidence", "Fully remote", "Materials missing"} <= labels


def test_triage_badges_include_generated_materials() -> None:
    triage = build_package_triage(_package("SAP ABAP", materials_generated=True, material_status="generated"))

    labels = {badge["label"] for badge in triage["triage_badges"]}
    assert "Materials generated" in labels


def test_triage_summary_prefers_ai_summary_and_risk_prefers_ai_flags() -> None:
    triage = build_package_triage(
        _package(
            "SAP ABAP",
            recommended_angle="Deterministic angle",
            concerns=["Deterministic concern"],
            ai_summary="AI summary",
            ai_risk_flags=["AI risk"],
        )
    )

    assert triage["primary_summary"] == "AI summary"
    assert triage["primary_risk"] == "AI risk"


def test_triage_summary_and_risk_fall_back_to_deterministic_fields() -> None:
    triage = build_package_triage(
        _package("SAP ABAP", recommended_angle="Use ABAP/RAP angle", concerns=["Language requirement unclear"])
    )

    assert triage["primary_summary"] == "Use ABAP/RAP angle"
    assert triage["primary_risk"] == "Language requirement unclear"
    assert "Language risk" in {badge["label"] for badge in triage["triage_badges"]}


def _event(
    event_type: str,
    *,
    source_index: int,
    source_count: int,
    source: str,
    jobs_found: int = 0,
    warnings_count: int = 0,
    elapsed_time_seconds: float | None = None,
    message: str | None = None,
) -> dict:
    counts = {
        "source_index": source_index,
        "source_count": source_count,
        "jobs_found": jobs_found,
        "warnings_count": warnings_count,
    }
    if elapsed_time_seconds is not None:
        counts["elapsed_time_seconds"] = elapsed_time_seconds
    return {
        "event_type": event_type,
        "phase": "source_ingestion",
        "current_source": source,
        "message": f"{event_type} for {source}" if message is None else message,
        "counts": counts,
        "timestamp": f"2026-05-07T10:0{source_index}:00+00:00",
    }


def _package(
    title: str,
    *,
    score: int = 80,
    category: str = "strong",
    ai_should_prioritize: bool = False,
    ai_fit_confidence: str = "",
    remote: str = "",
    material_status: str = "missing",
    materials_generated: bool = False,
    recommended_angle: str = "",
    concerns: list[str] | None = None,
    ai_summary: str = "",
    ai_risk_flags: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "match_score": score,
        "match_category": category,
        "ai_should_prioritize": ai_should_prioritize,
        "ai_fit_confidence": ai_fit_confidence,
        "remote": remote,
        "material_status": material_status,
        "materials_generated": materials_generated,
        "recommended_angle": recommended_angle,
        "concerns": concerns or [],
        "ai_summary": ai_summary,
        "ai_risk_flags": ai_risk_flags or [],
        "state": "new",
        "application_status": "unreviewed",
        "rate": "EUR 800/day",
    }
