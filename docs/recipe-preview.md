# Recipe Preview

Recipe preview is a manual review tool for inspecting what one constrained recipe extracts before deciding whether it is trustworthy enough for later source integration.

Preview does not enable daily-run use, does not add recipes to `sources/recruiting-sites.yaml`, and does not change configured sources. It runs a selected recipe against one input only:

- a saved local calibration artifact
- a local fixture
- one explicitly provided public URL, using the same single-URL behavior as `test-recipe`

The preview output includes the recipe name and experimental status, extraction mode, quality summary, warnings, and extracted job fields such as title, URL, location, remote/work arrangement, rate/pay, workload/work type, language, start date, notes, and a description preview.

## Current Recipe Status

- Eursap: live-calibrated experimental; `static_html`; single vacancy anchors plus regex pattern extraction from saved local artifacts.
- Whitehall: live-calibrated experimental; `static_html`; `div.job-item` listing blocks from saved local artifacts.
- Montreal: partial rendered experimental; `rendered_html`; saved rendered `li.job-data` entries, but broad results include non-SAP roles.

## Example Commands

```powershell
python -m job_agent.cli test-recipe sources/recipes/experimental/eursap-jobs.yaml output/recipe-calibration/20260508-081750-eursap-eu-jobs/page.html --base-url https://eursap.eu/jobs
```

```powershell
python -m job_agent.cli test-recipe sources/recipes/experimental/whitehall-sap-contract.yaml output/recipe-calibration/20260508-081748-www-whitehallresources-com-sap-jobs-contract/page.html --base-url https://www.whitehallresources.com/sap-jobs/contract/
```

```powershell
python -m job_agent.cli test-recipe sources/recipes/experimental/montreal-associates-jobs.yaml output/recipe-calibration/20260508-081801-www-montrealassociates-com-uk-candidates-job-search/page.html --base-url https://www.montrealassociates.com/uk/candidates/job-search/
```

## Boundaries

- Does not call Claude or any LLM.
- Does not generate or edit recipes automatically.
- Does not enable recipes by default.
- Does not connect recipe sources to daily runs.
- Does not paginate, crawl, or traverse a site.
- Does not submit applications or fill forms.
