from __future__ import annotations

from job_agent.web.view_models.runs import (
    build_option_summary,
    build_package_triage,
    build_source_progress,
    build_triage_packages,
)


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
    assert items[0]["is_running"] is False
    assert items[0]["jobs_found"] == 3
    assert items[0]["elapsed_time_seconds"] == 0.25
    assert items[1]["status"] == "waiting"
    assert items[1]["is_running"] is False
    assert items[1]["source_name"] == "Waiting source 2/2"
    assert progress["summary"]["total_sources"] == 2
    assert progress["summary"]["sources_completed"] == 1
    assert progress["summary"]["jobs_found_so_far"] == 3


def test_option_summary_labels_full_source_ingestion() -> None:
    labels = build_option_summary(
        {
            "full_source_ingestion": True,
            "include_disabled_sources": True,
            "detail_extraction_limit": None,
        }
    )

    assert "Full source ingestion" in labels
    assert "Includes sources currently eligible outside daily runs" in labels
    assert "Reviews all new jobs in detail" in labels


def test_option_summary_labels_detail_limit_as_per_source() -> None:
    labels = build_option_summary({"detail_extraction_limit": 25})

    assert "Reviews up to 25 new jobs in detail per source" in labels


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
    assert progress["items"][0]["is_running"] is False
    assert progress["items"][1]["is_running"] is True


def test_source_progress_keeps_skipped_setup_sources_visible() -> None:
    progress = build_source_progress(
        [
            {
                "event_type": "source_setup_skipped",
                "phase": "source_ingestion",
                "current_source": "Daily-run setup",
                "message": "Skipped sources still in setup: Blocked Jobs (readiness is blocked).",
                "counts": {
                    "source_index": 0,
                    "source_count": 1,
                    "warnings_count": 1,
                    "skipped_sources": [
                        {
                            "source_name": "Blocked Jobs",
                            "source_id": "blocked-jobs",
                            "reason": "readiness is blocked",
                        }
                    ],
                },
                "timestamp": "2026-05-07T10:00:00+00:00",
            },
            _event("source_started", source_index=1, source_count=1, source="Ready Jobs"),
        ]
    )

    assert progress["summary"]["selected_sources"] == 1
    assert progress["summary"]["total_sources"] == 2
    assert progress["summary"]["sources_skipped_setup"] == 1
    assert progress["items"][1]["source_name"] == "Blocked Jobs"
    assert progress["items"][1]["status"] == "deferred"
    assert progress["items"][1]["source_id"] == "blocked-jobs"
    assert progress["items"][1]["source_action_href"] == "/sources/blocked-jobs"
    assert progress["items"][1]["source_action_label"] == "Open source"
    assert progress["items"][1]["skipped_reason"] == "readiness is blocked"


def test_source_progress_exposes_skipped_source_access_action() -> None:
    progress = build_source_progress(
        [
            {
                "event_type": "source_setup_skipped",
                "phase": "source_ingestion",
                "current_source": "Daily-run setup",
                "message": "Skipped sources still in setup: Dice (requires a connected session).",
                "counts": {
                    "source_index": 0,
                    "source_count": 0,
                    "warnings_count": 1,
                    "skipped_sources": [
                        {
                            "source_name": "Dice",
                            "source_id": "dice",
                            "reason": "dice.com requires a connected session",
                            "source_access_status": "needs_login",
                            "source_action_href": "/sources/dice/session",
                            "source_action_label": "Connect session",
                        }
                    ],
                },
                "timestamp": "2026-05-07T10:00:00+00:00",
            }
        ]
    )

    item = progress["items"][0]
    assert item["status"] == "deferred"
    assert item["source_access_status"] == "needs_login"
    assert item["source_action_href"] == "/sources/dice/session"
    assert item["source_action_label"] == "Connect session"


def test_source_progress_exposes_active_source_access_wait_action() -> None:
    progress = build_source_progress(
        [
            _event("source_started", source_index=1, source_count=1, source="Dice"),
            _event(
                "source_access_waiting",
                source_index=1,
                source_count=1,
                source="Dice",
                message="Waiting for source access for Dice: dice.com requires a connected session.",
                source_id="dice",
                source_access_status="needs_login",
                source_action_href="/sources/dice/session",
                source_action_label="Connect session",
            ),
        ]
    )

    item = progress["items"][0]
    assert item["status"] == "waiting"
    assert item["stage"] == "Waiting for source access"
    assert item["source_action_href"] == "/sources/dice/session"
    assert item["source_action_label"] == "Connect session"
    assert item["highlight"]["kind"] == "warning"
    assert progress["summary"]["sources_waiting"] == 1


def test_source_progress_exposes_listing_and_detail_coverage() -> None:
    progress = build_source_progress(
        [
            _event(
                "source_activity",
                source_index=1,
                source_count=1,
                source="Paged Jobs",
                jobs_found=20,
                page_explored_count=2,
                page_total=5,
                visible_total_job_count=50,
            ),
            _event(
                "detail_review_started",
                source_index=1,
                source_count=1,
                source="Paged Jobs",
                phase="source_processing",
                reviewed_in_detail_count=12,
                detail_total=12,
            ),
            _event(
                "detail_review_pause",
                source_index=1,
                source_count=1,
                source="Paged Jobs",
                phase="source_processing",
                detail_read_count=6,
                detail_total=12,
                message="Pausing 20s after 6 detail page(s) for Paged Jobs.",
            ),
        ]
    )

    item = progress["items"][0]
    assert item["listing_progress_text"] == "2/5 pages"
    assert item["coverage_text"] == "20/50"
    assert item["detail_progress_text"] == "6/12 pages"
    assert item["stage"] == "Pausing detail reads"


def test_source_activity_message_preserves_live_counts() -> None:
    progress = build_source_progress(
        [
            _event(
                "source_activity",
                source_index=1,
                source_count=1,
                source="Slow Board",
                message="Pagination page read",
                jobs_found=42,
                page_explored_count=3,
                page_total=9,
            ),
        ]
    )

    item = progress["items"][0]
    assert item["latest_message"] == "Pagination page read (3/9 listing pages; 42 listing(s) seen)."
    assert item["listing_progress_text"] == "3/9 pages"


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
    phase: str = "source_ingestion",
    **extra_counts,
) -> dict:
    counts = {
        "source_index": source_index,
        "source_count": source_count,
        "jobs_found": jobs_found,
        "warnings_count": warnings_count,
    }
    counts.update(extra_counts)
    if elapsed_time_seconds is not None:
        counts["elapsed_time_seconds"] = elapsed_time_seconds
    return {
        "event_type": event_type,
        "phase": phase,
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
