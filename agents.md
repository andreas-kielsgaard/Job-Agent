# Agent Guide

This project is a local-first SAP job preparation agent. It discovers postings, scores them, prepares review material, and exposes a local FastAPI/Jinja web UI. It must not become an application bot: do not add behavior that submits applications, creates accounts, logs in, bypasses captcha or bot protection, uploads CVs, or sends emails.

Use this file as the fast startup path. Use the companion docs for deeper routing:

- `docs/agent-info-map.md`: where to read for each product area.
- `docs/agent-test-map.md`: which tests to run for touched files.

## First Steps

1. Run `git status --short` and treat existing changes as user-owned.
2. Skim `README.md` for the current product surface and `docs/how-it-works.md` for the core data flow.
3. Pick the relevant area in `docs/agent-info-map.md`.
4. Before editing, search with `rg` and prefer existing service, workflow, and view-model patterns.
5. After editing, run the targeted tests in `docs/agent-test-map.md`; run `python -m pytest` or `.\scripts\check.ps1` for broad changes.

## Operating Boundaries

- Private user data belongs in ignored `profile/`, `.env`, `output/`, `jobs/seen_jobs.json`, and `jobs/application_status.json`. Committed examples live in `profile.example/`.
- Automated tests use temp project roots and block accidental external network/API calls. Do not weaken those guards.
- Source recipes and browser diagnostics fetch only explicit public URLs. Do not add crawling, hidden endpoint discovery, protected-page automation, or login bypass behavior.
- Daily runs can write packages, digests, run records, and seen state. Source tests and recipe previews are intentionally low-risk and should not write normal run outputs.
- Deterministic scoring is the source of truth. AI review can summarize and flag risks, but should not silently replace deterministic match decisions.

## Architecture Map

- `job_agent/cli.py`: command wiring for daily runs, recipe checks, source tests, candidates, and source enablement.
- `job_agent/run_service.py`: daily run orchestration, scoring, package writing, AI search summaries, events, and run records.
- `job_agent/sources.py`: adapter bridge for local YAML, generic HTML, and recipe-backed source execution.
- `job_agent/scoring.py`, `job_agent/highlights.py`, `job_agent/generator.py`: match scoring, highlight reasons, and generated materials.
- `job_agent/store.py`, `job_agent/run_store.py`, `job_agent/application_status_store.py`, `job_agent/io/`: local persistence.
- `job_agent/services/`: domain services for setup, materials, source registry, source tests, recipe generation, candidates, readiness, stats, and review bundles.
- `job_agent/services/recipes/`: constrained recipe models, YAML mapping, fetching, extraction, pagination, quality checks, and source-test insight.
- `job_agent/web/app.py`: app creation and router registration only.
- `job_agent/web/routers/`: HTTP boundaries. Keep these thin.
- `job_agent/web/workflows.py` and `job_agent/web/source_workflow.py`: controller/workflow state. Routes should delegate cross-screen decisions here.
- `job_agent/web/view_models/`: template context builders.
- `job_agent/web/templates/` and `job_agent/web/static/`: presentation layer.
- `sources/`: source registry, source health, execution config, and recipe YAML.
- `prompts/`, `templates/`, `profile.example/`: AI prompts, Markdown output templates, and public sample profile data.

## Workflow Rules

- Routes may validate form inputs, redirect, return JSON, and render templates. Decisions about source readiness, recipe lifecycle, setup state, and run state belong in workflow handlers or services.
- Recipe calibration, recipe generation, candidate approval, adoption, source health, source-test readiness, and enablement are separate steps. Do not collapse them.
- Source health answers whether a recipe extracted useful jobs from preview evidence. Go-live readiness answers whether the configured execution source works through the adapter path without writing outputs.
- Viewing source pages must not mutate execution config. Creating or refreshing execution entries must be an explicit action and should keep sources disabled until readiness gates pass.
- If you touch generated-material wording, check recruiter-facing templates for internal score leakage.

## Verification

Use targeted tests while iterating:

```powershell
python -m pytest tests/test_scoring.py
python -m pytest tests/test_web_smoke.py
```

Run the default product suite before finishing broad or cross-layer work:

```powershell
python -m pytest
```

Run lint, format check, and coverage when the change is ready for full verification:

```powershell
.\scripts\check.ps1
```

Exploratory tests under `tests/exploratory/` are excluded by default and may depend on saved/live source evidence. Run them only when the task explicitly concerns those probes.
