from __future__ import annotations

import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEBUG_PATH = Path("output/debug/frontend-state.json")
MAX_EVENTS = 80
_LOCK = threading.Lock()


def record_debug_event(
    root: Path,
    *,
    feature: str,
    action: str,
    state: dict[str, Any],
    request_path: str = "",
    method: str = "",
) -> dict[str, Any]:
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "feature": feature,
        "action": action,
        "method": method,
        "path": request_path,
        "state": _jsonable(state),
    }
    with _LOCK:
        payload = load_debug_state(root)
        payload["debug_file"] = str((root / DEBUG_PATH).resolve())
        payload["updated_at"] = event["timestamp"]
        payload.setdefault("latest_by_feature", {})[feature] = event
        events = payload.setdefault("events", [])
        events.append(event)
        payload["events"] = events[-MAX_EVENTS:]
        _write_debug_state(root, payload)
    return event


def load_debug_state(root: Path) -> dict[str, Any]:
    path = root / DEBUG_PATH
    if not path.exists():
        return {
            "debug_file": str(path.resolve()),
            "updated_at": "",
            "latest_by_feature": {},
            "events": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "debug_file": str(path.resolve()),
            "updated_at": "",
            "latest_by_feature": {},
            "events": [],
            "warning": "Existing debug state file could not be read and will be replaced on the next event.",
        }
    return data if isinstance(data, dict) else {"events": []}


def _write_debug_state(root: Path, payload: dict[str, Any]) -> None:
    path = root / DEBUG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
