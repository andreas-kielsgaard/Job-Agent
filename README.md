# SAP Job Agent

A local preparation agent for SAP freelance and contract roles.

It is designed to help you review opportunities, not to apply automatically. The daily run:

1. Loads your structured profile and preferences.
2. Reads configured job sources.
3. Fetches or imports job postings.
4. Deduplicates jobs that were already seen.
5. Scores each role against your profile.
6. Generates a tailored at-a-glance CV, application text, and form-answer draft.
7. Writes a daily digest to `output/daily-digests/`.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item -Recurse profile.example profile
python -m job_agent.cli run-daily
```

The first run uses the sample jobs in `jobs/raw/sample_jobs.yaml`, so you can see the output immediately.

## Optional Claude Setup

The agent works without an LLM, but generation is better with one.

1. Copy `.env.example` to `.env`.
2. Set `ANTHROPIC_API_KEY`.
3. Optionally change `CLAUDE_MODEL`. The default is `claude-sonnet-4-20250514`, which is a stable model ID rather than a moving alias.

```powershell
Copy-Item .env.example .env
notepad .env
python -m job_agent.cli run-daily --use-llm
```

If the key is missing or the Claude call fails, the agent falls back to deterministic text.

## Daily Automation On Windows

Once the manual run works, schedule it with Windows Task Scheduler:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "C:\Users\user\Documents\Job Agent\scripts\run_daily.ps1"
```

Start manually first. Scheduled automation is much easier to trust after you have inspected a few generated digests.

## Important Files

- `profile/`: your source-of-truth career data, preferences, skills, and writing style.
- `profile.example/`: public placeholder profile files. Copy this to `profile/` locally and edit the private copy.
- `sources/recruiting-sites.yaml`: job boards and recruiter sites to scan.
- `templates/`: Jinja templates for CVs, applications, and daily digest.
- `job_agent/`: Python implementation.
- `jobs/raw/sample_jobs.yaml`: sample input for smoke testing.
- `output/`: generated materials.
- `SECURITY.md`: how to keep API keys out of the public repo.

## Private Profile Data

This repo is designed for a public GitHub repository. Real personal data should live only in `profile/`, and `profile/` is ignored by Git.

Commit `profile.example/`, not `profile/`.

## What This Version Does Not Do

- It does not log in to job sites.
- It does not bypass captchas.
- It does not submit applications.
- It does not upload CVs.

Those are deliberate boundaries. The agent prepares a clean review package; you stay the final filter.
