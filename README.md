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
7. Writes a daily digest and an excluded/weak-role summary.

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
```

Claude use is explicit in generated `match-analysis.md` notes. If the key is missing or a call fails, the output says that deterministic fallback text was used.

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
- included and excluded daily outputs
- deterministic fallback generation
- lightweight tests

## Tests

```powershell
python -m unittest discover -s tests
```

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
