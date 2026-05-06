from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from job_agent.config import ROOT
from job_agent.services.cv_reference_service import CvReferenceService

router = APIRouter()


@router.get("/profile-files/{filename}")
def profile_file(filename: str) -> FileResponse:
    try:
        path = CvReferenceService(ROOT).resolve_profile_file(filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Profile file not found") from None
    return FileResponse(path, filename=path.name)
