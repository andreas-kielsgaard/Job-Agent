# Agent Guide

This project is a local-first SAP job preparation agent. It discovers postings, scores them, prepares review material, and exposes a local FastAPI/Jinja web UI. It must not become an application bot: do not add behavior that submits applications, creates accounts, logs in, bypasses captcha or bot protection, uploads CVs, or sends emails.

Use this file as the fast startup path. Use the companion docs for deeper routing:

- `docs/agent-info-map.md`: where to read for each product area.
- `docs/handler-map.md`: how handlers, services, runtime tasks, view models, routes, and templates usually share cross-screen state.
- `docs/agent-test-map.md`: which tests to run for touched files.

## First Steps

1. Run `git status --short` and treat existing changes as user-owned.
2. Skim `README.md` for the current product surface and `docs/how-it-works.md` for the core data flow.
3. Pick the relevant area in `docs/agent-info-map.md`.
4. Before editing, search with `rg` and prefer existing service, workflow, and view-model patterns.
5. After editing, run tests through `python app/environment/scripts/test_handler.py`, passing the targeted test files from `docs/agent-test-map.md`; use `.\app\environment\scripts\check.ps1` for the normal lint, format, and fast-test gate.

## Operating Boundaries

- Private user data belongs in ignored `user/profile/`, `user/.env`, `user/uploads/cv/`, `runtime/output/`, `runtime/jobs/seen_jobs.json`, and `runtime/jobs/application_status.json`. Committed examples live in `setup/defaults/profile/`.
- Automated tests use temp project roots and block accidental external network/API calls. Do not weaken those guards.
- Source recipes and browser diagnostics fetch only explicit public URLs. Do not add crawling, hidden endpoint discovery, protected-page automation, or login bypass behavior.
- Daily runs can write packages, digests, run records, and seen state. Source tests and recipe previews are intentionally low-risk and should not write normal run outputs.
- Deterministic scoring is the source of truth. AI review can summarize and flag risks, but should not silently replace deterministic match decisions.

## Architecture Map

- `app/code/job_agent/cli.py`: command wiring for daily runs, recipe checks, source tests, candidates, and source enablement.
- `app/code/job_agent/run_service.py`: daily run orchestration, scoring, package writing, AI search summaries, events, and run records.
- `app/code/job_agent/sources.py`: adapter bridge for local YAML, generic HTML, and recipe-backed source execution.
- `app/code/job_agent/scoring.py`, `app/code/job_agent/highlights.py`, `app/code/job_agent/generator.py`: match scoring, highlight reasons, and generated materials.
- `app/code/job_agent/store.py`, `app/code/job_agent/run_store.py`, `app/code/job_agent/application_status_store.py`, `app/code/job_agent/io/`: local persistence.
- `app/code/job_agent/services/`: domain services for setup, materials, source registry, source tests, recipe generation, candidates, readiness, stats, and review bundles.
- `app/code/job_agent/services/recipes/`: constrained recipe models, YAML mapping, fetching, extraction, pagination, quality checks, and source-test insight.
- `app/code/job_agent/web/app.py`: app creation and router registration only.
- `app/code/job_agent/web/routers/`: HTTP boundaries. Keep these thin.
- `app/code/job_agent/web/workflows.py` and `app/code/job_agent/web/source_workflow.py`: controller/workflow state. Routes should delegate cross-screen decisions here.
- `app/code/job_agent/web/view_models/`: template context builders.
- `app/code/job_agent/web/templates/` and `app/code/job_agent/web/static/`: presentation layer.
- `user/sources/`: local source registry, source health, execution config, and recipe YAML copied from `setup/defaults/sources/`.
- `app/resources/prompts/`, `app/resources/templates/`, `setup/defaults/profile/`: AI prompts, Markdown output templates, and public sample profile data.

## Workflow Context

- Routes may validate form inputs, redirect, return JSON, and render templates. Decisions about readiness, lifecycle, setup state, and run state are usually easier to reason about when they are projected by workflow handlers or services.
- Many features span routes, services, runtime tasks, view models, templates, and static JS. `docs/handler-map.md` is an orientation map for those handoffs, useful when checking whether a page-specific condition already has a shared handler or state projection.
- Recipe calibration, recipe generation, candidate approval, adoption, source health, source-test readiness, and enablement are modeled as separate steps in the app.
- Source health answers whether a recipe extracted useful jobs from preview evidence. Go-live readiness answers whether the configured execution source works through the adapter path without writing outputs.
- Source page views are expected to be read-only for execution config. Creating or refreshing execution entries is handled through explicit actions, and sources stay disabled until readiness gates pass.
- Generated-material wording can affect recruiter-facing templates, so those templates are useful context when changing summaries, caveats, or scoring language.

## Verification

Use targeted tests while iterating:

```powershell
python app/environment/scripts/test_handler.py tests/test_scoring.py
python app/environment/scripts/test_handler.py tests/test_web_smoke.py
```

Run the fast product suite before a normal handoff:

```powershell
python app/environment/scripts/test_handler.py --fast
```

Run all non-exploratory tests before finishing broad or cross-layer work:

```powershell
python app/environment/scripts/test_handler.py --full
```

The test handler invokes pytest with progress breadcrumbs enabled, prints per-file progress, and writes `.pytest-progress/latest.txt` plus `.pytest-progress/latest.json`. Raw `python -m pytest` does not write those files unless `--job-agent-progress` or `JOB_AGENT_PYTEST_PROGRESS=1` is set. If a handler run times out or an agent resets, run `python app/environment/scripts/test_handler.py --show-progress` or inspect that progress file before rerunning so already-passed files do not need to be repeated.

Run lint, format check, and fast tests when the change is ready for normal verification:

```powershell
.\app\environment\scripts\check.ps1
```

Run coverage plus repo-state mutation auditing for release/audit verification:

```powershell
.\app\environment\scripts\check-release.ps1
```

Exploratory tests under `tests/exploratory/` are excluded by default and may depend on saved/live source evidence. Run them only when the task explicitly concerns those probes.

