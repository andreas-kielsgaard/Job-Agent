from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from job_agent.services.ai_edit_service import AiEditRequest, AiEditService
from job_agent.web.dependencies import current_root

router = APIRouter()


@router.get("/api/ai-edit/context")
def ai_edit_context(field_id: str, button_id: str, job_id: str = "", run_id: str = "") -> JSONResponse:
    return JSONResponse(AiEditService(current_root()).context_payload(field_id, button_id, job_id, run_id))


@router.post("/api/ai-edit/generate")
async def ai_edit_generate(request: Request) -> JSONResponse:
    data = await request.json()
    service = AiEditService(current_root())
    ai_request = AiEditRequest(
        field_id=data.get("field_id", ""),
        button_id=data.get("button_id", data.get("field_id", "")),
        current_text=data.get("current_text", ""),
        user_instruction=data.get("user_instruction", ""),
        selected_blocks=data.get("selected_blocks", []),
        disabled_blocks=data.get("disabled_blocks", []),
        job_id=data.get("job_id", ""),
        run_id=data.get("run_id", ""),
    )
    try:
        result = service.generate(ai_request)
        return JSONResponse({"ok": True, **result})
    except Exception as exc:
        package = service.packages.find_package(ai_request.job_id, ai_request.run_id) if ai_request.job_id else None
        files = service.packages.read_package_files(package) if package else {}
        prompt = service.provider.build_prompt(
            field_id=ai_request.field_id,
            current_text=ai_request.current_text,
            user_instruction=ai_request.user_instruction,
            selected_blocks=ai_request.selected_blocks,
            disabled_blocks=ai_request.disabled_blocks,
            job_package=package,
            job_files=files,
        )
        return JSONResponse({"ok": False, "error": str(exc), "prompt": prompt}, status_code=400)
