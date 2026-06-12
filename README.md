# SAP Job Agent

A local-first preparation agent for SAP freelance and contract roles.

The boundary is intentional: this agent prepares review material. It does not submit applications, create accounts, log in, upload CVs to job sites, bypass captchas, or send emails.

## Start Here

After pulling the repo, use the launcher for your OS:

```text
Start-JobAgent-Windows.bat
Start-JobAgent-Mac.command
```

On first run the launcher:

1. Finds Python 3.11+.
2. Asks before attempting to install Python if it is missing.
3. Creates `app/environment/.venv`.
4. Installs `app/environment/requirements.txt`.
5. Applies first-run defaults from `setup/defaults/`.
6. Creates local private folders under `user/` and generated state under `runtime/`.
7. Starts the FastAPI web UI and opens `http://127.0.0.1:8765/`.

The setup flow is safe to test from a fresh checkout because defaults are copied into ignored local folders instead of editing the committed examples.

## Folder Layout

```text
Start-JobAgent-Windows.bat
Start-JobAgent-Mac.command
README.md
app/
  code/job_agent/              Python application package
  resources/
    prompts/                   Prompt templates
    templates/                 Markdown/Jinja material templates
    jobs/raw/                  Sample job YAML
  environment/
    requirements*.txt          Dependency sets
    scripts/                   Developer and launcher scripts
setup/
  defaults/
    profile/                   Public starter profile copied on first run
    sources/                   Starter source registry, recipes, and execution config
    .env.example               Starter env file copied to user/.env
user/                          Ignored local setup
  profile/                     Private profile content
  uploads/cv/                  Uploaded reference CV files
  sources/                     Local editable source setup
  .env                         Local API keys and model preferences
runtime/                       Ignored generated state
  output/                      Runs, packages, digests, logs, diagnostics
  jobs/                        Seen jobs, application status, source listing index
docs/
tests/
```

Legacy temp roots used by tests can still use `profile/`, `sources/`, `jobs/`, `output/`, `templates/`, and `prompts/`; the app resolves both layouts.

## What It Does

The daily run:

1. Loads your private structured profile from `user/profile/`, or setup defaults if no private profile exists.
2. Reads enabled sources from `user/sources/recruiting-sites.yaml`.
3. Imports jobs from local YAML, bounded public HTML, or approved recipe-backed sources.
4. Records whether a job is new, changed, or previously seen.
5. Scores roles with deterministic SAP contract matching.
6. Optionally uses Claude for AI-enhanced review summaries and generated materials.
7. Writes review packages, digests, run records, event streams, and local status files under `runtime/`.

## Local Web UI

The web UI supports setup, profile-based source suggestions, source review, test runs, daily runs, job triage, generated-material review, manual posting intake, match sandboxing, stats, and local profile editing.

The server binds to `127.0.0.1` and is intended for one local user, not public hosting.

## CLI Use

The launcher is the normal path. For direct CLI work from PowerShell:

```powershell
$env:PYTHONPATH = "$PWD\app\code"
.\app\environment\.venv\Scripts\python.exe -m job_agent.cli run-daily --include-seen
```

For macOS/Linux:

```bash
export PYTHONPATH="$PWD/app/code"
./app/environment/.venv/bin/python -m job_agent.cli run-daily --include-seen
```

Use `--mark-seen` only after you are comfortable with the output.

## Optional Claude Setup

The agent works without Claude. Deterministic matching and fallback materials remain available.

To configure Claude, open Setup in the web UI or edit `user/.env`:

```text
ANTHROPIC_API_KEY=your_private_key
CLAUDE_MODEL=claude-sonnet-4-6
CLAUDE_USE_BY_DEFAULT=false
```

AI review is advisory. Deterministic scoring remains the source of truth for match category and inclusion decisions.

## Optional Playwright Diagnostics

Playwright is only for explicit browser diagnostics and recipe/source setup probes.

```powershell
.\app\environment\.venv\Scripts\python.exe -m pip install -r app\environment\requirements-playwright.txt
.\app\environment\.venv\Scripts\python.exe -m playwright install chromium
.\app\environment\.venv\Scripts\python.exe app\environment\scripts\check_playwright.py
```

Artifacts are written under `runtime/output/`.

## Private Data

Commit:

```text
setup/defaults/
app/resources/
```

Do not commit:

```text
user/
runtime/
app/environment/.venv/
```

`user/profile/` is where real name, contact details, address, CV narrative, skills, preferences, and work history belong. `user/uploads/cv/` stores the full reference CV used as evidence for setup and manual upload convenience.

## Development

Run tests from the repo root:

```powershell
python -m pytest
```

Run lint/format checks:

```powershell
python -m ruff check .
python -m ruff format --check .
```

The default test suite excludes exploratory live-source probes.

Useful docs:

- `docs/how-it-works.md`: core data flow.
- `docs/agent-info-map.md`: where to read before changing each area.
- `docs/agent-test-map.md`: targeted test selection.
