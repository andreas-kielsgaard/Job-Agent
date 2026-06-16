# Handler Map

This is an orientation note for cross-app workflows. It is not a complete ownership registry, and it is not meant to replace reading the code around the change. Use it to find where shared state is usually assembled before adding another page-local interpretation of the same workflow.

## Usual Shape

- Routes in `app/code/job_agent/web/routers/` are the HTTP boundary. They parse request data, call workflow or service helpers, redirect, return JSON, or render templates.
- Workflow handlers in `app/code/job_agent/web/workflows.py`, `app/code/job_agent/web/source_workflow.py`, and nearby modules assemble screen-ready state that is reused across pages.
- Domain services in `app/code/job_agent/services/` own durable operations such as setup, recipe generation, source tests, source registry updates, material writing, and source suggestions.
- Runtime helpers in `app/code/job_agent/web/runtime.py` and related modules coordinate background work and expose progress to the UI.
- View models in `app/code/job_agent/web/view_models/` shape service and workflow data for templates without making HTTP decisions.
- Templates and static JS in `app/code/job_agent/web/templates/` and `app/code/job_agent/web/static/` render and refresh state that should already be expressed by handlers, services, or view models.
- Stores in `app/code/job_agent/*store*.py`, `app/code/job_agent/io/`, `user/sources/`, and `runtime/` persist local state. The workflow layer often combines several stores into one user-facing status.

## Cross-App Handlers

- `AppWorkflowHandler` in `app/code/job_agent/web/workflows.py` is the broad composition point for setup, dashboard, profile, source, run, package, and suggestion state.
- `SourceWorkflowHandler` in `app/code/job_agent/web/source_workflow.py` gathers source overview/detail state, source-test payloads, readiness, source setup state, listing-index status, and auto-setup eligibility signals.
- `SourceAutoSetupWorkflowHandler` in `app/code/job_agent/web/source_auto_setup.py` coordinates procedural automatic source preparation runs and presents their queue/progress state.
- Recipe generation services in `app/code/job_agent/services/recipe_*` cover calibration, candidate artifacts, candidate policy, approval, adoption, generation status, and recipe-specific health.
- Source execution services in `app/code/job_agent/services/source_*` cover registry data, source tests, readiness, listing indexes, sessions, suggestions, disqualification, and URL assessment.
- Run and ingest paths use `app/code/job_agent/run_service.py`, `app/code/job_agent/run_store.py`, `app/code/job_agent/web/runtime.py`, `app/code/job_agent/web/routers/runs.py`, and `app/code/job_agent/web/view_models/runs.py`.
- Profile and setup paths use `setup_service.py`, CV/profile draft services, setup routers, setup guide services, and setup view models.
- Work status widgets use `app/code/job_agent/web/work_widgets.py`, runtime state, `base.html`, and shared static styles/scripts to show long-running work across pages.

## Common Handoff Patterns

- A page route often asks a workflow handler for a context object, then passes that object directly into a template.
- A JSON polling route often asks the same workflow/runtime handler for a compact status projection so browser updates match the full page view.
- A long-running operation usually starts from a route, records state through a service or runtime helper, and becomes visible through a shared status endpoint.
- A source setup or recipe flow may have separate concepts of generated evidence, selected recipe, source health, source-test readiness, daily-run enablement, and seen-state. Those concepts can appear together in one screen, but they are intentionally stored and evaluated separately.
- LLM-assisted features normally keep deterministic service state as the primary record, with AI output attached as suggestions, summaries, refinements, or review material.

## Reading Order

1. Start with the router that receives the request or renders the page.
2. Follow the call into workflow handlers or view models that assemble the user-facing state.
3. Check the service that writes or evaluates the underlying domain state.
4. Look for matching JSON/status endpoints used by dynamic UI updates.
5. Pick tests from `docs/agent-test-map.md` that cover both the service behavior and the web projection.
