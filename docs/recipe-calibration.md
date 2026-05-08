# Recipe Calibration

Live recipe validation showed that fixture selectors can pass while the real recruiter DOM uses different structures. Some pages expose jobs as heading/link blocks, table-like rows, or single anchors containing title, IDs, location, language, dates, work type, and pay.

Calibration captures one public page so selectors and patterns can be adjusted manually. It does not generate recipes, edit recipes, crawl pages, inspect hidden endpoints, bypass access controls, or integrate anything into daily runs.

## What It Saves

`calibrate-recipe` writes artifacts under `output/recipe-calibration/`:

- `page.html`
- `visible-text.txt`
- `candidate-elements.html`
- `selector-report.json`
- `summary.md`

Candidate discovery is deterministic. It highlights likely job regions, heading/link blocks, table rows, and single-link text blobs, while marking likely navigation, filters, and CTA blocks as noise.

## Patterns

Recipes can include optional `patterns` for Eursap-style text blobs:

```yaml
patterns:
  title_regex: "^(?P<title>.+?)\\s+Job ID:"
  job_id_regex: "Job ID:\\s*(?P<job_id>\\d+)"
  location_regex: "Location:\\s*(?P<location>[^:]+?)\\s+Language:"
  language_regex: "Language:\\s*(?P<language>[^:]+?)\\s+Start date:"
  start_date_regex: "Start date:\\s*(?P<start_date>[^:]+?)\\s+Work type:"
  work_type_regex: "Work type:\\s*(?P<work_type>[^:]+?)\\s+Pay:"
  rate_regex: "Pay:\\s*(?P<rate>.+)$"
```

If a regex has a named group matching the field, that value is used. Otherwise the first captured group is used. Invalid regexes fail recipe validation.

## Commands

```powershell
python -m job_agent.cli calibrate-recipe https://www.whitehallresources.com/sap-jobs/contract/ --recipe sources/recipes/experimental/whitehall-sap-contract.yaml --static
```

```powershell
python -m job_agent.cli calibrate-recipe https://www.montrealassociates.com/uk/candidates/job-search/ --recipe sources/recipes/experimental/montreal-associates-jobs.yaml --rendered
```

```powershell
python -m job_agent.cli calibrate-recipe https://eursap.eu/jobs --static
```

Rendered mode requires Playwright. Static mode works with the base dependencies.

## Boundaries

- Fetches only the provided public URL.
- Rendered mode renders only that same URL.
- Does not use login, cookies, sessions, captcha bypass, bot-protection bypass, or private/local URLs.
- Does not inspect hidden endpoints or network APIs.
- Does not paginate, recurse, or crawl broadly.
- Does not call Claude or any LLM.
- Does not edit recipes automatically.
