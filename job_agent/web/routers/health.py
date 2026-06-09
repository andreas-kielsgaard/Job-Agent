from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from job_agent.web.runtime import runtime

router = APIRouter()


@router.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse(runtime.health_payload())


@router.get("/api/work-status")
def work_status() -> JSONResponse:
    return JSONResponse(runtime.active_work_payload())
