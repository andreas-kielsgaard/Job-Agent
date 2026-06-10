from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from job_agent.config import ROOT
from job_agent.web.dependencies import cv_reference_service

router = APIRouter()


@router.get("/profile-files/{filename}")
def profile_file(filename: str) -> FileResponse:
    try:
        path = cv_reference_service().resolve_profile_file(filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Profile file not found") from None
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else None
    return FileResponse(path, filename=path.name, media_type=media_type, content_disposition_type="inline")


@router.get("/docs/{filename}")
def docs_file(filename: str) -> FileResponse:
    path = ROOT / "docs" / filename
    if path.parent != ROOT / "docs" or not path.exists() or path.suffix.lower() != ".md":
        raise HTTPException(status_code=404, detail="Documentation file not found")
    return FileResponse(path, filename=path.name, media_type="text/markdown", content_disposition_type="inline")
