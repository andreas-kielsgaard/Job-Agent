from __future__ import annotations

from types import SimpleNamespace

from job_agent.services.recipes.fetching import (
    _rendered_capture_warnings,
    _rendered_request_blocks_snapshot,
    _wait_for_rendered_snapshot_ready,
)


def test_rendered_request_tracking_blocks_job_api_but_ignores_telemetry() -> None:
    assert _rendered_request_blocks_snapshot(
        SimpleNamespace(resource_type="xhr", url="https://www.experis.pl/api/services/Jobs/searchjobs")
    )
    assert not _rendered_request_blocks_snapshot(
        SimpleNamespace(resource_type="fetch", url="https://region1.google-analytics.com/g/collect")
    )
    assert not _rendered_request_blocks_snapshot(
        SimpleNamespace(resource_type="document", url="https://www.experis.pl/en/search")
    )


def test_rendered_snapshot_waits_for_pending_request_and_stable_html() -> None:
    pending = {1}
    page = _FakeRenderedPage(
        html_by_tick=[
            "<main>Loading...</main>",
            "<main>Loading...</main>",
            "<main><article>SAP ABAP Consultant</article></main>",
            "<main><article>SAP ABAP Consultant</article></main>",
            "<main><article>SAP ABAP Consultant</article></main>",
        ],
        clear_pending_at_tick=2,
        pending=pending,
    )

    warnings = _wait_for_rendered_snapshot_ready(
        page,
        pending,
        timeout_ms=1_000,
        quiet_ms=0,
        stable_poll_count=2,
        poll_ms=1,
    )

    assert warnings == []
    assert page.wait_count >= 4
    assert page.content() == "<main><article>SAP ABAP Consultant</article></main>"


def test_networkidle_timeout_is_not_a_warning_when_snapshot_is_stable() -> None:
    assert _rendered_capture_warnings(networkidle_timed_out=True, snapshot_warnings=[]) == []


def test_snapshot_warning_survives_networkidle_timeout() -> None:
    warnings = ["Rendered page was still changing near the snapshot timeout; captured best available HTML."]

    assert _rendered_capture_warnings(networkidle_timed_out=True, snapshot_warnings=warnings) == warnings


class _FakeRenderedPage:
    def __init__(self, *, html_by_tick: list[str], clear_pending_at_tick: int, pending: set[int]) -> None:
        self.html_by_tick = html_by_tick
        self.clear_pending_at_tick = clear_pending_at_tick
        self.pending = pending
        self.tick = 0
        self.wait_count = 0

    def content(self) -> str:
        return self.html_by_tick[min(self.tick, len(self.html_by_tick) - 1)]

    def wait_for_timeout(self, _milliseconds: int) -> None:
        self.wait_count += 1
        self.tick += 1
        if self.tick >= self.clear_pending_at_tick:
            self.pending.clear()
