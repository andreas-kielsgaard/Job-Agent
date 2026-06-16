from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from job_agent.llm import LlmService
from job_agent.services.manual_posting_service import ManualPostingInput, ManualPostingService
from job_agent.web.dependencies import current_root, templates

router = APIRouter()


@router.get("/postings/new", response_class=HTMLResponse)
def new_posting(request: Request) -> HTMLResponse:
    root = current_root()
    return templates.TemplateResponse(
        request,
        "posting_new.html",
        {"request": request, "title": "Add Posting", "llm_configured": LlmService(root).is_configured()},
    )


@router.post("/postings/new")
def create_posting(
    title: str = Form(""),
    source: str = Form(""),
    company: str = Form(""),
    url: str = Form(""),
    application_url: str = Form(""),
    location: str = Form(""),
    remote: str = Form(""),
    rate: str = Form(""),
    workload: str = Form(""),
    posted_date: str = Form(""),
    description: str = Form(""),
    ai_enhanced_search: bool = Form(False),
    generate_materials: bool = Form(False),
    use_llm: bool = Form(False),
    llm_model: str = Form(""),
) -> RedirectResponse:
    try:
        result = ManualPostingService(current_root()).import_posting(
            ManualPostingInput(
                title=title,
                source=source,
                company=company,
                url=url,
                application_url=application_url,
                location=location,
                remote=remote,
                rate=rate,
                workload=workload,
                posted_date=posted_date,
                description=description,
                ai_enhanced_search=ai_enhanced_search,
                generate_materials=generate_materials,
                use_llm=use_llm,
                llm_model=llm_model,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/jobs/{result.stable_id}?run_id={result.run.run_id}", status_code=303)
