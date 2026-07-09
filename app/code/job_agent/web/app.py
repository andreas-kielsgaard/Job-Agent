from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from job_agent.web.dependencies import WEB_DIR
from job_agent.web.routers import (
    ai_edit,
    applications,
    compatibility,
    dashboard,
    debug,
    files,
    health,
    jobs,
    match_sandbox,
    postings,
    recipe_editor,
    runs,
    setup,
    setup_guide,
    sources,
    stats,
)
from job_agent.web.runtime import runtime


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    runtime.startup()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Job Agent", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.middleware("http")
    async def track_activity(request: Request, call_next):
        runtime.mark_activity()
        return await call_next(request)

    app.include_router(health.router)
    app.include_router(dashboard.router)
    app.include_router(runs.router)
    app.include_router(stats.router)
    app.include_router(jobs.router)
    app.include_router(applications.router)
    app.include_router(match_sandbox.router)
    app.include_router(postings.router)
    app.include_router(compatibility.router)
    app.include_router(recipe_editor.router)
    app.include_router(debug.router)
    app.include_router(sources.router)
    app.include_router(setup_guide.router)
    app.include_router(setup.router)
    app.include_router(files.router)
    app.include_router(ai_edit.router)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "job_agent.web.app:app",
        host="127.0.0.1",
        port=int(os.getenv("JOB_AGENT_PORT", "8765")),
        reload=False,
    )
