from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from job_agent.web.dependencies import cv_reference_service

router = APIRouter()


@router.get("/profile-files/{filename}")
def profile_file(filename: str) -> FileResponse:
    try:
        path = cv_reference_service().resolve_profile_file(filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Profile file not found") from None
    return FileResponse(path, filename=path.name)
