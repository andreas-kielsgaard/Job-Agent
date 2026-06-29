from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from job_agent.llm import LlmService
from job_agent.services.application_index_service import ApplicationIndexService
from job_agent.services.gmail_thread_posting_service import GmailThreadPostingService
from job_agent.services.manual_posting_service import ManualPostingInput, ManualPostingService
from job_agent.web.dependencies import current_root, templates, workflow_handler

router = APIRouter()


@router.get("/postings/new", response_class=HTMLResponse)
def new_posting(request: Request) -> HTMLResponse:
    root = current_root()
    return templates.TemplateResponse(
        request,
        "posting_new.html",
        {"request": request, "title": "Add Posting", "llm_configured": LlmService(root).is_configured()},
    )


@router.get("/postings/email-threads", response_class=HTMLResponse)
def email_thread_postings(request: Request) -> HTMLResponse:
    root = current_root()
    threads = GmailThreadPostingService(root).list_thread_options()
    applications = ApplicationIndexService(root).list_rows()
    return templates.TemplateResponse(
        request,
        "posting_email_threads.html",
        {
            "request": request,
            "title": "Add Posting From Gmail",
            "threads": threads,
            "applications": applications,
            "llm_configured": LlmService(root).is_configured(),
            "message": request.query_params.get("message", ""),
            "warning": request.query_params.get("warning", ""),
        },
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


@router.post("/postings/email-threads/import")
def create_posting_from_email_thread(
    thread_id: str = Form(...),
    ai_enhanced_search: bool = Form(False),
    generate_materials: bool = Form(False),
    use_llm: bool = Form(False),
    llm_model: str = Form(""),
) -> RedirectResponse:
    try:
        result = GmailThreadPostingService(current_root()).import_thread(
            thread_id,
            ai_enhanced_search=ai_enhanced_search,
            generate_materials=generate_materials,
            use_llm=use_llm,
            llm_model=llm_model,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Gmail thread not found in local cache") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/jobs/{result.stable_id}?run_id={result.run.run_id}", status_code=303)


@router.post("/postings/email-threads/link")
def link_email_thread_to_application(
    thread_id: str = Form(...),
    application_id: str = Form(...),
    account_id: str = Form(""),
) -> RedirectResponse:
    try:
        workflow_handler().applications.link_gmail_thread(application_id, thread_id, account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Application record not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"/applications/{quote(application_id, safe='')}?{urlencode({'message': 'Gmail thread linked.'})}#communication",
        status_code=303,
    )
