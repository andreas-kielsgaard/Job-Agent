from __future__ import annotations

from job_agent.web.view_models.runs import build_source_progress


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
            _event("source_completed", source_index=1, source_count=1, source="HTML", warnings_count=1),
        ]
    )

    item = progress["items"][0]
    assert item["status"] == "warning"
    assert item["warnings_count"] == 1
    assert "source_completed" in item["latest_message"]
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


def _event(
    event_type: str,
    *,
    source_index: int,
    source_count: int,
    source: str,
    jobs_found: int = 0,
    warnings_count: int = 0,
    elapsed_time_seconds: float | None = None,
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
        "message": f"{event_type} for {source}",
        "counts": counts,
        "timestamp": f"2026-05-07T10:0{source_index}:00+00:00",
    }
