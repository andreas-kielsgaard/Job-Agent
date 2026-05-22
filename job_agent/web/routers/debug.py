from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from job_agent.web.debug_state import load_debug_state, record_debug_event
from job_agent.web.dependencies import current_root
from job_agent.web.runtime import runtime

router = APIRouter()


@router.get("/api/debug/frontend-state")
def get_frontend_debug_state() -> JSONResponse:
    root = current_root()
    payload = load_debug_state(root)
    payload["active_server"] = {
        "app_version": runtime.app_version,
        "root": str(root),
        "health": runtime.health_payload(),
    }
    return JSONResponse(payload)


@router.post("/api/debug/frontend-state")
async def post_frontend_debug_state(request: Request) -> JSONResponse:
    payload: dict[str, Any] = await request.json()
    feature = str(payload.get("feature") or "frontend")
    action = str(payload.get("action") or "browser_state")
    event = record_debug_event(
        current_root(),
        feature=feature,
        action=action,
        method=request.method,
        request_path=str(payload.get("page_url") or request.url.path),
        state={
            "browser": payload,
            "active_server": {
                "app_version": runtime.app_version,
                "root": str(current_root()),
            },
        },
    )
    return JSONResponse({"ok": True, "recorded_at": event["timestamp"]})
