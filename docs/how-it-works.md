# How The Agent Works

This repo is a preparation agent, not an application bot.

## Mental Model

There are three practical layers:

1. Discovery: collect job postings from configured sources.
2. Matching and generation: score each role and create tailored materials.
3. Review package: write a daily digest for you to review manually.

The current implementation runs those layers inside one command:

```powershell
$env:PYTHONPATH = "$PWD\app\code"
.\app\environment\.venv\Scripts\python.exe -m job_agent.cli run-daily --use-llm
```

## Data Flow

```text
user/profile/*.yaml + user/profile/*.md
  or setup/defaults/profile/*.yaml + setup/defaults/profile/*.md
  + user/sources/recruiting-sites.yaml
  + app/resources/jobs/raw/sample_jobs.yaml or live source fetches
        |
        v
job_agent.sources
        |
        v
job_agent.store deduplication
        |
        v
job_agent.scoring
        |
        v
job_agent.generator
        |
        v
runtime/output/YYYY-MM-DD/<role>/
runtime/output/daily-digests/YYYY-MM-DD-digest.md
```

## What Claude Does

Claude is used only for the parts where language judgment helps:

- writing a concise application text
- adapting tone to the role
- handling partial matches honestly
- turning your canonical profile into role-specific wording

The agent does not need Claude for:

- loading profile data
- reading sources
- deduplicating jobs
- scoring obvious keywords
- rendering templates
- saving outputs

That split is deliberate. The deterministic parts keep the run inspectable; Claude improves the writing.

## First Setup

1. Run the launcher:

```text
Start-JobAgent-Windows.bat
Start-JobAgent-Mac.command
```

2. The launcher creates `app/environment/.venv`, copies `setup/defaults/profile` to `user/profile`, copies `setup/defaults/sources` to `user/sources`, creates `user/.env`, and starts the local web UI.

3. Put your Claude key in Setup or `user/.env`:

```text
ANTHROPIC_API_KEY=your_key_here
CLAUDE_MODEL=claude-sonnet-4-6
```

4. Run a test:

```powershell
.\app\environment\.venv\Scripts\python.exe -m job_agent.cli run-daily --include-seen --use-llm
```

5. Review the digest:

```text
runtime/output/daily-digests/
```

6. When you trust the output, run with seen-state updates:

```powershell
.\app\environment\.venv\Scripts\python.exe -m job_agent.cli run-daily --use-llm --mark-seen
```

## Customizing Your Profile

The most important files are:

- `user/profile/contact.yaml`
- `user/profile/preferences.yaml`
- `user/profile/skills.yaml`
- `user/profile/experience.yaml`
- `user/profile/canonical-cv.md`
- `user/profile/writing-style.md`

The better these files are, the less the agent has to guess.

For a public repo, keep real profile data in `user/profile/`. That folder is ignored by Git. The committed placeholder version lives in `setup/defaults/profile/`.

## Adding Job Sources

Start conservative:

1. Add sources to `user/sources/recruiting-sites.yaml` or through the Sources UI.
2. Keep new live sources as `enabled: false` until tested.
3. Prefer one site-specific scraper at a time.

Some sites render dynamically or block scraping. Those should be handled later with browser automation, not brittle HTML guessing.

## Scheduling

After manual testing, use Windows Task Scheduler to run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "C:\Users\user\Documents\Job Agent\app\environment\scripts\run_daily.ps1"
```

Daily scheduling should come after you have reviewed several manual digests.
