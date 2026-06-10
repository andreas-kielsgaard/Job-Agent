from __future__ import annotations

import os
from pathlib import Path

from job_agent.io.atomic import atomic_write_text


def test_atomic_write_text_retries_transient_replace_permission_error(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "run.json"
    calls = {"count": 0}
    original_replace = os.replace

    def flaky_replace(source, destination):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError("temporarily locked")
        return original_replace(source, destination)

    monkeypatch.setattr("job_agent.io.atomic.os.replace", flaky_replace)

    atomic_write_text(path, '{"status": "running"}')

    assert calls["count"] == 3
    assert path.read_text(encoding="utf-8") == '{"status": "running"}'
