from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from job_agent.io.json_store import read_json
from job_agent.paths import runtime_dir
from job_agent.web.runtime import runtime

router = APIRouter()

_LAUNCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _json_response(request: Request, payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    if request.headers.get("origin") == "null":
        response.headers["Access-Control-Allow-Origin"] = "null"
        response.headers["Vary"] = "Origin"
    return response


@router.get("/api/health")
def health(request: Request) -> JSONResponse:
    return _json_response(request, runtime.health_payload())


@router.get("/api/work-status")
def work_status() -> JSONResponse:
    return JSONResponse(runtime.active_work_payload())


@router.get("/api/launcher-ready/{launch_id}")
def launcher_ready(request: Request, launch_id: str) -> JSONResponse:
    if not _LAUNCH_ID_RE.fullmatch(launch_id):
        return _json_response(request, {"ready": False, "target": "", "message": "Invalid launcher token."})
    path = runtime_dir(runtime.root) / "launcher" / f"{launch_id}.json"
    payload = read_json(path, {}, strict=False)
    if not isinstance(payload, dict):
        payload = {}
    return _json_response(
        request,
        {
            "ready": bool(payload.get("ready")),
            "target": str(payload.get("target") or ""),
            "health": str(payload.get("health") or ""),
            "launch_id": launch_id,
            "ready_at": str(payload.get("ready_at") or ""),
        },
    )


@router.get("/api/events")
def app_events(after: int = 0) -> StreamingResponse:
    def stream_events():
        revision = max(0, int(after or 0))
        while True:
            for event in runtime.wait_for_changes(revision):
                revision = max(revision, int(event.get("revision") or 0))
                payload = json.dumps(event, ensure_ascii=False)
                yield f"id: {revision}\nevent: app-change\ndata: {payload}\n\n"

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/api/app-version")
def app_version() -> JSONResponse:
    return JSONResponse(runtime.app_version_payload())


@router.post("/api/app-version/restart")
def restart_app(request: Request) -> JSONResponse:
    payload = runtime.request_restart()
    return _json_response(request, payload, status_code=200 if payload.get("ok") else 503)
