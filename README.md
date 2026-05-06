# SAP Job Agent

A local-first preparation agent for SAP freelance and contract roles.

The boundary is intentional: this agent prepares review material. It does not submit applications, create accounts, log in, upload CVs, bypass captchas, or send emails.

## What It Does Today

The daily run:

1. Loads your private structured profile from `profile/`, or placeholder data from `profile.example/`.
2. Reads enabled sources from `sources/recruiting-sites.yaml`.
3. Imports jobs from stable local YAML sources and best-effort public HTML sources.
4. Records whether a job is new, changed, or previously seen.
5. Scores roles with component-based SAP contract matching.
6. Generates recruiter-facing materials for included roles.
7. Writes a daily digest, excluded/weak-role summary, per-run log, run registry entry, event stream, token usage records, and package indexes.

Generated package per included role:

```text
cv-at-a-glance.md
application.md
form-answers.md
match-analysis.md
job.json
match.json
```

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item -Recurse profile.example profile
python -m job_agent.cli run-daily --include-seen
```

The first run uses `jobs/raw/sample_jobs.yaml`, which includes:

- strong ABAP/RAP match
- Fiori-adjacent partial match
- exploratory SAP project manager role
- language-mismatch role
- old/expired role

## Optional Claude Setup

The agent works without Claude. Deterministic fallbacks are always available.

To improve application text:

```powershell
Copy-Item .env.example .env
notepad .env
python -m job_agent.cli run-daily --include-seen --use-llm
```

Set:

```text
ANTHROPIC_API_KEY=your_private_key
CLAUDE_MODEL=claude-sonnet-4-20250514
CLAUDE_USE_BY_DEFAULT=false
```

Claude use is explicit in generated `match-analysis.md` notes. If the key is missing or a call fails, the output says that deterministic fallback text was used.

## Local Web UI

Start the frontend on localhost:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m job_agent.web.app
```

Open:

```text
http://127.0.0.1:8765
```

On Windows you can also double-click:

```text
scripts/open_frontend.bat
```

The launcher starts the web app only if it is not already running, then opens the browser. In launcher mode, the web app shuts itself down after 2 minutes with no open page activity and no active agent run.

The web UI supports:

- launching a daily run with options
- overview dashboard with latest-run status, daily-run action, jobs found today, unreviewed jobs, and applications in the last 7 days
- test runs that are hidden from normal run views and never mark jobs as seen
- skipping automatic CV/application/form generation to save tokens, then generating materials manually from a job page
- monitoring the active run through run events and logs
- reviewing previous runs
- opening generated job packages
- viewing match analysis, CV, application text, and form answers
- copying generated text
- copying a non-AI external-agent review bundle from each job page
- editing generated CV, application, form answers, and match analysis directly in the browser
- browsing all unique jobs across runs from the Jobs page
- bulk-updating selected jobs to unreviewed/seen, interesting, not interesting, applied, or archived
- viewing a Stats page with run, package, match, and application-status metrics
- archiving, soft-deleting, restoring, and viewing archived/deleted/test runs from the Runs page
- using "Edit with AI" on setup text blocks and job materials with a reusable context-aware prompt builder
- selecting which context blocks are included for each AI edit button, with saved per-button preferences
- marking jobs as `unreviewed`, `interesting`, `not_interesting`, `applied`, or `archived`
- guided setup for common profile and preference fields
- address setup with street address, post code, city, country, and kommune
- choosing a Claude model from simplified quality/cost/speed options
- editing local setup files such as skills, experience, sources, templates, and prompts
- uploading a full reference CV as PDF, DOCX, TXT, or Markdown
- extracting uploaded CV text into `profile/canonical-cv.md` when requested

The server binds to `127.0.0.1` by default. It is intended for one local user, not public hosting.

### Setup Guidance

The setup page is designed for low-technical editing first:

- Profile basics and availability/preferences use normal form fields.
- Sources can be enabled/disabled or added through a simple source form.
- Advanced files such as skills, experience, templates, and prompts remain editable as text with inline instructions.
- Template variables are documented on the setup page. Recruiter-facing templates should avoid internal score language.
- Template variable docs are hidden behind a clickable reference so the page stays approachable.
- "Edit with AI" buttons build prompts from relevant context such as app purpose, profile data, canonical CV, skills, experience, writing style, job JSON, and match data.

The uploaded reference CV is stored under ignored `profile/files/` and is linked from each posting detail page so it is easy to upload manually alongside the generated at-a-glance CV.

The default Claude setting is the Sonnet 4 alias `claude-sonnet-4-0`, so it tracks the newest Sonnet 4 snapshot. Anthropic notes that aliases are convenient for development, while stable model IDs such as `claude-sonnet-4-20250514` are better when consistent output matters.

## Private Data

This repo is designed to be safe as a public GitHub repository.

Commit:

```text
profile.example/
.env.example
```

Do not commit:

```text
profile/
.env
output/
jobs/seen_jobs.json
jobs/application_status.json
```

`profile/` is where your real name, contact details, address, CV, skills, preferences, and work history belong. It is ignored by Git.

## Source Ingestion

Sources are configured in `sources/recruiting-sites.yaml`.

Current adapters:

- `local_yaml`: reliable smoke-test and manual-import source.
- `generic_html` / `search_page`: best-effort public HTML link extraction.
- `WhitehallResourcesAdapter`: site-specific hook currently backed by generic extraction until selectors are tested.

The generic HTML adapter is conservative. If it cannot find plausible job links, it returns a source warning instead of inventing a fake job from the whole page.

## Scoring

Scoring is split into components:

- technical match
- module match
- contract fit
- location fit
- seniority fit
- leadership/project-management interest
- language risk
- frontend or functional risk
- freshness risk
- rate visibility or fit

Categories:

- `strong`
- `exploratory`
- `weak`
- `excluded`

Freshness rules:

- exclude postings older than 4 months
- exclude deadlines more than 3 weeks overdue
- mark missing dates as uncertain

## Prototype vs Production-Ish

Prototype behavior:

- sample YAML jobs are the most reliable source
- generic HTML extraction only finds obvious public links
- standard form answers are generic and not based on inspected forms
- no PDF/DOCX rendering yet

Production-ish behavior already present:

- private profile and secret handling
- structured job model
- source adapter pattern
- component scoring
- structured seen-job storage
- structured application-status storage
- run registry and per-run event logs
- package index JSON files for UI consumption
- token usage capture for Claude responses where available
- included and excluded daily outputs
- deterministic fallback generation
- lightweight tests

## Tests

```powershell
python -m unittest discover -s tests
```

Development tooling:

```powershell
pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format .
```

Basic smoke checks:

```powershell
python -m job_agent.cli run-daily --include-seen
python -m job_agent.web.app
```

## Architecture

The app is split into small local-first layers:

- `job_agent/run_service.py` orchestrates daily discovery, scoring, packaging, logging, and run summaries.
- `job_agent/run_store.py`, `store.py`, `application_status_store.py`, and `token_usage.py` persist local state as ignored JSON/JSONL files.
- `job_agent/services/` contains reusable operations for package indexes, generated materials, setup/profile editing, CV reference files, review bundles, stats, LLM calls, and AI-assisted editing.
- `job_agent/io/` contains atomic JSON/YAML/text write helpers used by local stores and services.
- `job_agent/web/app.py` only creates the FastAPI app, registers middleware/startup hooks, and includes routers.
- `job_agent/web/routers/` contains HTTP routes grouped by dashboard, runs, jobs, setup, files, stats, health, and AI edit.
- `job_agent/web/view_models/` builds template contexts so routes stay thin.
- `job_agent/web/formatting.py` contains small presentation helpers such as the current Markdown-to-HTML renderer.
- `job_agent/web/templates/` and `job_agent/web/static/` are the presentation layer.

Runtime output and private profile data remain local and ignored by Git.
Claude-backed AI edits are recorded in token usage when Anthropic returns usage data.
The test suite includes service/store boundary tests for run state, application status, setup, CV references, package indexes, review bundles, and basic web smoke checks.

## Daily Automation On Windows

Once manual runs look good, schedule:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "C:\Users\user\Documents\Job Agent\scripts\run_daily.ps1"
```

Start manually first. Scheduling is much easier to trust after you have reviewed several generated digests.

## Important Files

- `profile.example/`: public placeholder profile files.
- `profile/`: private local profile files, ignored by Git.
- `sources/recruiting-sites.yaml`: job source configuration.
- `jobs/raw/sample_jobs.yaml`: smoke-test job postings.
- `prompts/`: Claude prompt templates.
- `templates/`: recruiter-facing and internal Markdown templates.
- `job_agent/`: Python implementation.
- `output/`: generated materials, ignored by Git.
- `SECURITY.md`: secret and personal-data handling.
